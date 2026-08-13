@echo off
title SIGRS - Inicializando Sistema
cd /d "%~dp0backend"

echo Verificando ambiente Python...
if not exist "venv" (
    echo Criando ambiente virtual...
    python -m venv venv
)

echo Ativando ambiente...
call venv\Scripts\activate.bat

echo.
echo Instalando dependencias (Isso pode levar alguns minutos na primeira vez. Aguarde...)
echo =============================================================================
pip install fastapi uvicorn pydantic opencv-python-headless numpy --quiet
echo =============================================================================

echo.
echo INICIANDO SISTEMA SIGRS...
echo NAO FECHA ESTA JANELA PRETA! (Ela e o servidor).
start "" "%~dp0frontend\index.html"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
pause