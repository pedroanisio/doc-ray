-- doc-ray capability showcase — one query per NLP layer.
-- Run:  docker compose exec -T postgres psql -U docray -d docray -f /... (or pipe)
\pset footer off
\echo '======================================================================'
\echo ' doc-ray — full-stack capability showcase'
\echo '======================================================================'

\echo '\n[corpus] documents ingested, by language of their lexemes'
SELECT count(DISTINCT d.id) docs,
       (SELECT count(*) FROM sentences) sentences,
       (SELECT count(*) FROM tokens) tokens,
       (SELECT count(*) FROM lexemes) lexemes
FROM documents d WHERE d.status='extracted';

\echo '\n[layer 1: provenance] processing trail of one document'
SELECT p.kind, p.tool, p.tool_version
FROM document_provenance p JOIN documents d ON d.id=p.document_id
WHERE d.original_filename='AirFrance447_BEA.pdf' ORDER BY p.id;

\echo '\n[layers 3/6: tokens + UPOS] first tokens of AE-relativity'
SELECT t.ordinal, substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start) form,
       l.lemma, t.upos, t.xpos
FROM tokens t JOIN spans sp ON sp.id=t.span_id
JOIN annotations a ON a.id=t.annotation_id JOIN document_content c ON c.document_id=a.document_id
JOIN documents d ON d.id=a.document_id LEFT JOIN lexemes l ON l.uid=t.lexeme_uid
WHERE d.original_filename='AE-relativity.pdf' AND a.layer='spacy'
ORDER BY t.ordinal LIMIT 8;

\echo '\n[layer 7: morphology] UD FEATS on a verb token'
SELECT tf.feature, tf.value FROM token_feature tf
WHERE tf.token_id = (
  SELECT t.id FROM tokens t JOIN annotations a ON a.id=t.annotation_id
  JOIN documents d ON d.id=a.document_id
  WHERE d.original_filename='AE-relativity.pdf' AND t.upos='VERB' AND a.layer='spacy'
  LIMIT 1) ORDER BY tf.feature;

\echo '\n[layer 6: POS] corpus distribution (top UPOS)'
SELECT upos, count(*) FROM tokens WHERE upos IS NOT NULL GROUP BY upos ORDER BY count(*) DESC LIMIT 8;

\echo '\n[layer 10: dependency] most common grammatical relations'
SELECT deprel, count(*) FROM dependency GROUP BY deprel ORDER BY count(*) DESC LIMIT 8;

\echo '\n[layer 11: NER] entity types across the corpus'
SELECT entity_type, count(*) FROM entity_mention GROUP BY entity_type ORDER BY count(*) DESC LIMIT 10;

\echo '\n[layer 11: NER] sample PERSON/ORG mentions'
SELECT DISTINCT em.entity_type, substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start) mention
FROM entity_mention em JOIN spans sp ON sp.id=em.span_id
JOIN annotations a ON a.id=em.annotation_id JOIN document_content c ON c.document_id=a.document_id
WHERE em.entity_type IN ('PERSON','PER','ORG') AND length(substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start)) BETWEEN 4 AND 24
LIMIT 8;

\echo '\n[layer 13: coreference] largest coref chains (string-match)'
SELECT substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start) surface, count(*) mentions
FROM coref_mention cm JOIN coref_chain ch ON ch.id=cm.chain_id
JOIN spans sp ON sp.id=cm.span_id JOIN document_content c ON c.document_id=ch.document_id
GROUP BY ch.id, surface ORDER BY mentions DESC LIMIT 6;

\echo '\n[layer 8: chunks] noun-chunk count per layer'
SELECT chunk_type, count(*) FROM chunk GROUP BY chunk_type;

\echo '\n[layer 17: discourse] PDTB sense distribution'
SELECT sense, count(*) FROM discourse_relation GROUP BY sense ORDER BY count(*) DESC LIMIT 6;

\echo '\n[layer 18: temporal] most frequent time expressions'
SELECT value, count(*) FROM timex GROUP BY value ORDER BY count(*) DESC LIMIT 6;

\echo '\n[layer 19: sentiment] polarity split'
SELECT polarity, count(*) FROM sentiment GROUP BY polarity;

\echo '\n[layer 20: embeddings] 3 nearest documents to orwell1984 (pgvector cosine)'
WITH q AS (SELECT e.vector v FROM embedding e JOIN spans s ON s.id=e.span_id
           JOIN documents d ON d.id=s.document_id WHERE d.original_filename='orwell1984.pdf')
SELECT left(d.original_filename,40) doc, round((1-(e.vector <=> q.v))::numeric,3) cosine
FROM embedding e JOIN spans s ON s.id=e.span_id JOIN documents d ON d.id=s.document_id, q
WHERE d.original_filename <> 'orwell1984.pdf' ORDER BY e.vector <=> q.v LIMIT 3;

\echo '\n[multi-layer] annotation runs per document (rule + spaCy + heuristics coexisting)'
SELECT layer, tool, count(*) docs FROM annotations GROUP BY layer, tool ORDER BY layer, tool;
