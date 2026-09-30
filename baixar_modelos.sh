#!/bin/bash
# Baixa os modelos que não são versionados no repositório (por tamanho):
#   - face_landmarker.task  (MediaPipe, ~3,6 MB) — malha facial de 478 pontos
#   - tts_model/            (Piper, ~61 MB)      — voz reserva, sintetiza na hora
#   - tts_model/kokoro-*    (Kokoro, ~120 MB)    — voz das falas, só pra rodar gerar_vozes.py
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

echo "Baixando a voz Kokoro (usada por gerar_vozes.py)..."
kbase="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
curl -L --fail -o tts_model/kokoro-v1.0.int8.onnx "$kbase/kokoro-v1.0.int8.onnx"
curl -L --fail -o tts_model/voices-v1.0.bin       "$kbase/voices-v1.0.bin"

echo "Pronto. Modelos baixados."
