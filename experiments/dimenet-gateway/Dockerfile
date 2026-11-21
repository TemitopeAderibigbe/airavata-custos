# Licensed to the Apache Software Foundation (ASF) under the Apache License, Version 2.0.
# See gateway.py for the full license header.
FROM python:3.11-slim

WORKDIR /app

# CPU-only PyTorch, PyG, the torch-cluster wheel DimeNet++ needs for radius_graph,
# and TensorFlow (PyG converts the original TensorFlow DimeNet++ checkpoint).
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir torch_geometric==2.6.1 \
 && pip install --no-cache-dir --only-binary torch-cluster torch-cluster==1.6.3 \
        -f https://data.pyg.org/whl/torch-2.5.0+cpu.html \
 && pip install --no-cache-dir tensorflow-cpu==2.18.0 sympy \
        fastapi==0.115.6 uvicorn==0.32.1 "pyjwt[crypto]==2.10.1" requests==2.32.3

COPY gateway.py run_experiments.py ./

ENV DATA_DIR=/data
EXPOSE 8090
CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8090"]
