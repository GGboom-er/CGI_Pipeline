@echo off
title CGI Pipeline MCP Server
set PROJECT_ROOT=%~dp0..
cd /d "%PROJECT_ROOT%"

echo [CGI Pipeline] Starting MCP Server...
python mcp_server/server.py --http

pause
