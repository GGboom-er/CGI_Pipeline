@echo off
title CGI Pipeline MCP Server
set PROJECT_ROOT=%~dp0..
cd /d "%PROJECT_ROOT%"
set PYTHONPATH=%PROJECT_ROOT%\src;%PYTHONPATH%

echo [CGI Pipeline] Starting MCP Server...
python -m cgi_pipeline.server.server --http

pause
