"""
AppConfig for scores_app with background data preloading
"""
from django.apps import AppConfig
import threading
import time
import sys
from datetime import datetime


class ScoresAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "scores_app"
    
    def ready(self):
        """
        Called when Django starts up. Starts a background thread to preload today's data.
        """
        # Only run in the main process (not in reloader child process)
        import os
        if os.environ.get('RUN_MAIN') == 'true' or not sys.argv or 'runserver' not in sys.argv:
            sys.stderr.write("[PRELOADER] Starting background data preloader...\n")
            sys.stderr.flush()
            
            # Start background thread
            thread = threading.Thread(target=self.preload_data_periodically, daemon=True)
            thread.start()
    
    def preload_data_periodically(self):
        """
        Background thread that preloads today's data every 60 seconds.
        """
        from .nhl_scores import get_games_data
        
        while True:
            try:
                # Get today's date
                today = datetime.now().strftime('%Y-%m-%d')
                
                sys.stderr.write(f"[PRELOADER] Fetching today's data ({today})...\n")
                start_time = time.time()
                
                # Preload today's data - this will populate the cache
                data = get_games_data(today)
                
                elapsed = (time.time() - start_time) * 1000
                game_count = len(data.get('games', []))
                sys.stderr.write(f"[PRELOADER] Loaded {game_count} games in {elapsed:.0f}ms\n")
                sys.stderr.flush()
                
            except Exception as e:
                sys.stderr.write(f"[PRELOADER] Error loading data: {e}\n")
                sys.stderr.flush()
            
            # Wait 60 seconds before next refresh
            time.sleep(60)

