# voice/views.py
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from .models import Suggestion, Survey
from core.decorators import hr_required


@login_required
def suggestion_box(request):
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        if text:
            Suggestion.objects.create(text=text)
        return redirect('people_culture')
    return render(request, 'voice/suggestion_box.html')


@login_required
@hr_required
def suggestion_admin(request):
    suggestions = Suggestion.objects.all()
    return render(request, 'voice/suggestion_admin.html', {'suggestions': suggestions})


@login_required
@hr_required
def add_survey(request):
    if request.method == 'POST':
        Survey.objects.create(
            title=request.POST.get('title', ''),
            url=request.POST.get('url', ''),
            closes_on=request.POST.get('closes_on', '2099-12-31'),
            added_by=request.user,
        )
        return redirect('people_culture')
    return render(request, 'voice/add_survey.html')
