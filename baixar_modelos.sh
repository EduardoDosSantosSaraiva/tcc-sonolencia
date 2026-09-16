#!/bin/bash
# Baixa os modelos que não são versionados no repositório (por tamanho):
#   - face_landmarker.task  (MediaPipe, ~3,6 MB) — malha facial de 478 pontos
#   - tts_model/            (Piper, ~61 MB)      — voz pt-BR dos alertas
set -e
cd "$(dirname "$0")"

echo "Baixando o modelo de malha facial do MediaPipe..."
curl -L --fail -o face_landmarker.task \
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"

echo "Baixando a voz pt-BR do Piper (pt_BR-faber-medium)..."
mkdir -p tts_model
base="https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium"
curl -L --fail -o tts_model/pt_BR-faber-medium.onnx      "$base/pt_BR-faber-medium.onnx"
curl -L --fail -o tts_model/pt_BR-faber-medium.onnx.json "$base/pt_BR-faber-medium.onnx.json"

echo "Pronto. Modelos baixados."
