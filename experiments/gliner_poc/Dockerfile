# GLiNER POC image — derives from the built worker image so the CUDA/torch stack
# is byte-identical to what the annotation pipeline already runs on. Installing
# torch fresh here would risk a driver/CUDA mismatch that presents as
# "GPU not available" and silently falls back to CPU.
FROM doc-ray-worker:latest

USER root

# gliner pulls transformers/huggingface-hub; psycopg is already present in the
# worker image for the corpus read.
RUN pip install --no-cache-dir "gliner==0.2.28"

# Model + tokenizer downloads land on the scratch NVMe, not the container layer,
# so a re-run does not re-download and an image rebuild does not lose the cache.
ENV HF_HOME=/scratch/hf-cache \
    TRANSFORMERS_VERBOSITY=error \
    TOKENIZERS_PARALLELISM=false

WORKDIR /poc
COPY run_poc.py route_genres.py /poc/

ENTRYPOINT ["python3"]
