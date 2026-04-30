@echo off
REM Sobe o Jarvis a partir desse diretorio.
REM -u = unbuffered (prints aparecem em tempo real, nao em chunks)
cd /d "%~dp0"
py -u main.py
