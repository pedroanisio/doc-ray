-- doc-ray: Prism Tier-1 visual layer — deterministic image features (Prism §5.2).
--
-- Two additive tables on the existing corpus spine, document-scoped under the
-- same RLS as every NLP layer:
--   * document_image        — one row per detected image/region; crop metadata
--                             only (the crop bytes live in the object store).
--   * image_tier1_features  — the deterministic feature vector for one crop
--                             (palette/histogram/saturation/value/contrast/edge/
--                             class), produced by application/visual_features.py.
--
-- PALS posture: Tier-1 is a pure deterministic function of the crop bytes plus a
-- pinned tool version — no model, no review queue. `tool`/`tool_version` pin the
-- producer; re-running replaces prior rows for the same (document_image_id, tool)
-- via delete-then-COPY, exactly like the NLP layers. ULID PKs are app-supplied
-- (domain/ulid.new_ulid_str); document_id is uuid to match documents(id).

CREATE TABLE IF NOT EXISTS document_image (
    id                ulid PRIMARY KEY,
    document_id       uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number       int  NOT NULL CHECK (page_number > 0),
    source            text NOT NULL
                        CHECK (source IN ('embedded-raster', 'rendered-region', 'full-page')),
    xref              int,                       -- PDF xref for embedded rasters, else NULL
    bbox_x0           double precision,
    bbox_y0           double precision,
    bbox_x1           double precision,
    bbox_y1           double precision,
    width_px          int  NOT NULL CHECK (width_px > 0),
    height_px         int  NOT NULL CHECK (height_px > 0),
    object_key        text NOT NULL,             -- object-store key of the crop PNG
    sha256            char(64) NOT NULL,         -- content hash of the crop bytes
    detector          text NOT NULL,
    detector_version  text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, page_number, source, sha256)
);

CREATE INDEX IF NOT EXISTS idx_document_image_doc_page
    ON document_image (document_id, page_number);

SELECT docray_attach_rls('document_image'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT, INSERT, UPDATE, DELETE ON document_image TO docray_client;


CREATE TABLE IF NOT EXISTS image_tier1_features (
    id                ulid PRIMARY KEY,
    document_image_id ulid NOT NULL REFERENCES document_image(id) ON DELETE CASCADE,
    document_id       uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,  -- denormalized for RLS
    dominant_colors   jsonb NOT NULL,            -- [{hex, proportion}]
    histogram         jsonb NOT NULL,            -- 48 floats (16 bins x 3 channels)
    mean_saturation   real NOT NULL,
    mean_value        real NOT NULL,
    contrast          real NOT NULL,
    edge_density      real NOT NULL,
    image_class       text NOT NULL
                        CHECK (image_class IN ('photo', 'diagram', 'screenshot', 'line-art', 'chart')),
    aspect_ratio      real NOT NULL,
    width_px          int  NOT NULL,
    height_px         int  NOT NULL,
    exif              jsonb,
    tool              text NOT NULL,
    tool_version      text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_image_id, tool)
);

CREATE INDEX IF NOT EXISTS idx_image_tier1_features_doc
    ON image_tier1_features (document_id);

SELECT docray_attach_rls('image_tier1_features'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT, INSERT, UPDATE, DELETE ON image_tier1_features TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('089_visual_tier1.sql')
    ON CONFLICT (filename) DO NOTHING;
