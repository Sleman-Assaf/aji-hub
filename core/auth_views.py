# core/auth_views.py
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib import messages


def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect(request.GET.get('next', '/'))
        else:
            messages.error(request, 'Invalid username or password.')
    return render(request, 'core/login.html')


def logout_view(request):
    from django.contrib.auth import logout
    from django.utils import timezone

    # Close & auto-name the active AJiAssist session before logout
    if request.user.is_authenticated:
        try:
            from assist.models import AssistSession
            active = AssistSession.objects.filter(
                user=request.user, is_active=True
            ).prefetch_related('messages').first()
            if active and active.messages.exists():
                active.name     = active.auto_name()
                active.is_active = False
                active.ended_at  = timezone.now()
                active.save(update_fields=['name', 'is_active', 'ended_at'])
            elif active:
                # Empty session — just delete it
                active.delete()
        except Exception:
            pass

    logout(request)
    return redirect('login')
