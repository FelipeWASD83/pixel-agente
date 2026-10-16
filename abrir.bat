@echo off
cd /d "%~dp0"
python px.py janela
if errorlevel 1 (
  echo.
  echo Nao consegui abrir o Pixel Agente. Veja a mensagem acima.
  pause
)
