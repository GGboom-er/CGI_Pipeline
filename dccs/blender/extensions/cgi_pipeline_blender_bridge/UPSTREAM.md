# Upstream Attribution

The localhost JSON/TCP bridge and Blender main-thread request-queue pattern were
adapted from `djeada/blender-mcp-server` v0.1.3 at commit
`7eed33edf4aca2ab0ca84a6da27321f89f68b504`.

- Source: https://github.com/djeada/blender-mcp-server
- License: MIT
- Copyright: 2026 Adam Djellouli

This extension keeps only the bridge transport and synchronous Python execution
ideas. CGI Pipeline adds token authentication, multi-session port discovery,
its own request/response contract, and integration with the existing
`cgi_pipeline_mcp` server.

## MIT License Notice

Copyright (c) 2026 Adam Djellouli

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
