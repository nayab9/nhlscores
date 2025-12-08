# scores_app/views.py

# Import the capturing function from your script
# Note: Since nhl_scores.py is in the same directory, a relative import works.
from .nhl_scores import get_score_output
from django.shortcuts import render

def nhl_scores_view(request):
    """Executes the score script and displays the output on a webpage."""
    
    # 1. Call the wrapper function to get the output string
    script_output_text = get_score_output()

    context = {
        'script_output': script_output_text,
        'page_title': 'Today\'s NHL Scores',
    }

    # 2. Render the template
    # The template path is 'scores_app/nhl_scores_page.html'
    return render(request, 'scores_app/nhl_scores_page.html', context)
