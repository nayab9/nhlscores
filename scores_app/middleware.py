# scores_app/middleware.py
import sys
import time

class DebugLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        sys.stderr.write("[MIDDLEWARE] DebugLoggingMiddleware initialized\n")
        sys.stderr.flush()

    def __call__(self, request):
        sys.stderr.write("\n" + "="*80 + "\n")
        sys.stderr.write(f"[MIDDLEWARE] Request received: {request.method} {request.path}\n")
        sys.stderr.write(f"[MIDDLEWARE] Headers: {dict(request.headers)}\n")
        sys.stderr.flush()
        
        start_time = time.time()
        response = self.get_response(request)
        duration = (time.time() - start_time) * 1000
        
        sys.stderr.write(f"[MIDDLEWARE] Response: {response.status_code} ({duration:.0f}ms)\n")
        sys.stderr.write("="*80 + "\n\n")
        sys.stderr.flush()
        
        return response
