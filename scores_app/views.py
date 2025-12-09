# scores_app/views.py

from .nhl_scores import get_score_output, get_games_data, get_games_data_skeleton
from django.shortcuts import render
from django.http import JsonResponse
import time
import sys

def nhl_scores_view(request):
    """Displays NHL scores with a modern card-based interface."""
    start_time = time.time()
    sys.stderr.write("\n" + "="*80 + "\n")
    sys.stderr.write(f"[VIEW] nhl_scores_view() called at {time.strftime('%H:%M:%S')}\n")
    
    # Get date parameter from request (format: YYYY-MM-DD)
    date_str = request.GET.get('date', None)
    sys.stderr.write(f"[VIEW] Request date parameter: {date_str}\n")
    
    # Check if this is an AJAX request for JSON data
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    is_json = request.GET.get('format') == 'json'
    use_skeleton = request.GET.get('skeleton') == 'true'
    sys.stderr.write(f"[VIEW] Request type - AJAX: {is_ajax}, JSON: {is_json}, Skeleton: {use_skeleton}\n")
    
    if is_ajax or is_json:
        # Support skeleton mode for AJAX requests too (for fast date navigation)
        if use_skeleton:
            sys.stderr.write(f"[VIEW] Handling AJAX/JSON request with SKELETON mode...\n")
            data_start = time.time()
            data = get_games_data_skeleton(date_str)
            data_time = (time.time() - data_start) * 1000
            sys.stderr.write(f"[VIEW] get_games_data_skeleton() completed in {data_time:.0f}ms\n")
            sys.stderr.write(f"[VIEW] Returning JSON skeleton response with {len(data.get('games', []))} games\n")
        else:
            # Return FULL data for AJAX refresh (slow but complete)
            sys.stderr.write(f"[VIEW] Handling AJAX/JSON request, calling get_games_data()...\n")
            data_start = time.time()
            data = get_games_data(date_str)
            data_time = (time.time() - data_start) * 1000
            sys.stderr.write(f"[VIEW] get_games_data() completed in {data_time:.0f}ms\n")
            sys.stderr.write(f"[VIEW] Returning JSON response with {len(data.get('games', []))} games\n")
        return JsonResponse(data)
    
    # For regular page loads, return SKELETON data FAST (no slow API calls)
    sys.stderr.write(f"[VIEW] Handling regular page load, calling get_games_data_skeleton() for FAST response...\n")
    data_start = time.time()
    games_data = get_games_data_skeleton(date_str)
    data_time = (time.time() - data_start) * 1000
    sys.stderr.write(f"[VIEW] get_games_data_skeleton() completed in {data_time:.0f}ms\n")
    sys.stderr.write(f"[VIEW] Retrieved {len(games_data.get('games', []))} games (skeleton mode)\n")
    
    context = {
        'games_data': games_data,
        'page_title': 'NHL Scores',
    }
    
    sys.stderr.write(f"[VIEW] Rendering template...\n")
    view_total = (time.time() - start_time) * 1000
    sys.stderr.write(f"[VIEW] Total view execution time: {view_total:.0f}ms\n")
    sys.stderr.write("="*80 + "\n\n")
    sys.stderr.flush()
    
    return render(request, 'scores_app/nhl_scores_page.html', context)

