# cafeteria/views.py
import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import Notification

from .models import (
    CafeteriaOrder, CafeteriaOrderItem, CateringRequest, MenuCategory, MenuItem,
    MeetingPackage, PackageOrder, STAFF_GROUP,
    is_cafeteria_staff, is_facility_manager,
)


def _notify_staff(title, body, link):
    from django.contrib.auth.models import Group
    group = Group.objects.filter(name=STAFF_GROUP).first()
    if not group:
        return
    for user in group.user_set.all():
        Notification.objects.create(
            recipient=user, type=Notification.Type.INFO,
            title=title, body=body, link=link,
        )


# ─────────────────────────── Employee ordering ───────────────────────────

@login_required
def cafeteria_home(request):
    categories = MenuCategory.objects.filter(is_active=True).prefetch_related('items')
    my_orders = CafeteriaOrder.objects.filter(ordered_by=request.user).prefetch_related('lines')[:15]
    return render(request, 'cafeteria/home.html', {
        'categories': categories,
        'my_orders': my_orders,
        'is_staff_member': is_cafeteria_staff(request.user),
        'is_facility_manager': is_facility_manager(request.user),
    })


@login_required
@require_POST
def place_order(request):
    try:
        data = json.loads(request.body)
        raw_items = data.get('items', [])
        if not raw_items:
            return JsonResponse({'ok': False, 'error': 'Your cart is empty.'}, status=400)

        order = CafeteriaOrder.objects.create(
            ordered_by=request.user,
            delivery_location=(data.get('location') or '')[:200],
            notes=(data.get('notes') or '')[:255],
        )
        for entry in raw_items:
            item = MenuItem.objects.filter(pk=entry.get('id'), is_active=True).first()
            qty = max(1, min(int(entry.get('qty', 1)), 50))
            if item:
                CafeteriaOrderItem.objects.create(
                    order=order, item=item, item_name=item.name,
                    unit_price=item.price, quantity=qty,
                )
        if not order.lines.exists():
            order.delete()
            return JsonResponse({'ok': False, 'error': 'No valid items in cart.'}, status=400)

        _notify_staff(
            'New cafeteria order',
            f'{request.user.get_full_name()} — {order.summary()}',
            reverse('cafeteria:staff_dashboard'),
        )
        return JsonResponse({'ok': True, 'order_id': order.pk})
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)


# ─────────────────────────── Staff dashboard ───────────────────────────

@login_required
def staff_dashboard(request):
    if not is_cafeteria_staff(request.user):
        raise PermissionDenied
    tab = request.GET.get('tab', 'orders')
    context = {
        'tab': tab,
        'orders': CafeteriaOrder.objects.select_related('ordered_by').prefetch_related('lines')[:100],
        'package_orders': PackageOrder.objects.select_related('package', 'ordered_by')[:100],
        'categories': MenuCategory.objects.all().prefetch_related('items'),
        'packages': MeetingPackage.objects.all(),
        'order_statuses': CafeteriaOrder.Status.choices,
        'package_statuses': PackageOrder.Status.choices,
        'package_levels': MeetingPackage.Level.choices,
    }
    return render(request, 'cafeteria/staff_dashboard.html', context)


@login_required
@require_POST
def order_set_status(request, pk):
    if not is_cafeteria_staff(request.user):
        raise PermissionDenied
    order = get_object_or_404(CafeteriaOrder, pk=pk)
    status = request.POST.get('status')
    if status in CafeteriaOrder.Status.values:
        order.status = status
        order.save(update_fields=['status', 'updated_at'])
        Notification.objects.create(
            recipient=order.ordered_by, type=Notification.Type.INFO,
            title=f'Cafeteria order #{order.pk}: {order.get_status_display()}',
            body=order.summary(), link=reverse('cafeteria:home'),
        )
        messages.success(request, f'Order #{order.pk} set to {order.get_status_display()}.')
    return redirect(f"{reverse('cafeteria:staff_dashboard')}?tab=orders")


@login_required
@require_POST
def package_order_set_status(request, pk):
    if not is_cafeteria_staff(request.user):
        raise PermissionDenied
    order = get_object_or_404(PackageOrder, pk=pk)
    status = request.POST.get('status')
    if status in PackageOrder.Status.values:
        order.status = status
        order.save(update_fields=['status', 'updated_at'])
        Notification.objects.create(
            recipient=order.ordered_by, type=Notification.Type.INFO,
            title=f'Meeting package "{order.package.name}": {order.get_status_display()}',
            body=f'{order.meeting_title} — {order.meeting_date}', link=reverse('cafeteria:package_order'),
        )
        messages.success(request, f'Package order #{order.pk} set to {order.get_status_display()}.')
    return redirect(f"{reverse('cafeteria:staff_dashboard')}?tab=package-orders")


def _dec(value, default=None):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return default


@login_required
@require_POST
def item_save(request):
    """Create or update a menu item (staff only)."""
    if not is_cafeteria_staff(request.user):
        raise PermissionDenied
    pk = request.POST.get('pk')
    name = (request.POST.get('name') or '').strip()
    price = _dec(request.POST.get('price'))
    category_id = request.POST.get('category')
    description = (request.POST.get('description') or '').strip()

    if pk:  # update existing
        item = get_object_or_404(MenuItem, pk=pk)
        if name:
            item.name = name
        if price is not None:
            item.price = price
        if description or 'description' in request.POST:
            item.description = description
        if category_id:
            item.category_id = category_id
        if 'toggle' in request.POST:
            item.is_active = not item.is_active
        if request.FILES.get('image'):
            item.image = request.FILES['image']
        elif 'remove_image' in request.POST:
            item.image.delete(save=False)
            item.image = None
        item.save()
        messages.success(request, f'Item "{item.name}" updated.')
    else:  # create new
        if not (name and price is not None and category_id):
            messages.error(request, 'Name, price and category are required for a new item.')
        else:
            MenuItem.objects.create(
                name=name, price=price, category_id=category_id, description=description,
                image=request.FILES.get('image'),
            )
            messages.success(request, f'Item "{name}" added to the menu.')
    return redirect(f"{reverse('cafeteria:staff_dashboard')}?tab=items")


@login_required
@require_POST
def package_save(request):
    """Create or update a meeting package (staff only)."""
    if not is_cafeteria_staff(request.user):
        raise PermissionDenied
    pk = request.POST.get('pk')
    name = (request.POST.get('name') or '').strip()
    level = request.POST.get('level')
    price = _dec(request.POST.get('price_per_person'))
    description = (request.POST.get('description') or '').strip()

    if pk:
        package = get_object_or_404(MeetingPackage, pk=pk)
        if name:
            package.name = name
        if level in MeetingPackage.Level.values:
            package.level = level
        if price is not None:
            package.price_per_person = price
        if description or 'description' in request.POST:
            package.description = description
        if 'toggle' in request.POST:
            package.is_active = not package.is_active
        package.save()
        messages.success(request, f'Package "{package.name}" updated.')
    else:
        if not (name and level in MeetingPackage.Level.values and price is not None):
            messages.error(request, 'Name, level and price per person are required for a new package.')
        else:
            MeetingPackage.objects.create(
                name=name, level=level, price_per_person=price, description=description,
            )
            messages.success(request, f'Package "{name}" created.')
    return redirect(f"{reverse('cafeteria:staff_dashboard')}?tab=packages")


# ─────────────────────── Facility Manager: meeting packages ───────────────────────

@login_required
def package_order(request):
    if not is_facility_manager(request.user):
        raise PermissionDenied

    packages = MeetingPackage.objects.filter(is_active=True)

    if request.method == 'POST':
        package = MeetingPackage.objects.filter(pk=request.POST.get('package'), is_active=True).first()
        meeting_title = (request.POST.get('meeting_title') or '').strip()
        meeting_date = request.POST.get('meeting_date')
        meeting_time = request.POST.get('meeting_time')
        attendees = request.POST.get('attendees_count')
        if not (package and meeting_title and meeting_date and meeting_time and attendees):
            messages.error(request, 'Package, meeting title, date, time and attendees count are required.')
        else:
            order = PackageOrder.objects.create(
                package=package,
                ordered_by=request.user,
                meeting_title=meeting_title,
                meeting_room=(request.POST.get('meeting_room') or '')[:150],
                meeting_date=meeting_date,
                meeting_time=meeting_time,
                attendees_count=max(1, int(attendees)),
                notes=(request.POST.get('notes') or '')[:255],
            )
            _notify_staff(
                'New meeting package order',
                f'{package.get_level_display()} — {package.name} for "{order.meeting_title}" '
                f'({order.attendees_count} attendees) on {order.meeting_date} at {order.meeting_time}. Paid by AJi.',
                reverse('cafeteria:staff_dashboard') + '?tab=package-orders',
            )
            messages.success(request, f'Package "{package.name}" ordered for "{meeting_title}". Billed to AJi.')
            return redirect('cafeteria:package_order')

    my_orders = PackageOrder.objects.filter(ordered_by=request.user).select_related('package')[:25]
    catering_requests = (CateringRequest.objects
                         .exclude(status=CateringRequest.Status.WAITING_ROOM)
                         .select_related('package', 'requested_by', 'booking', 'booking__room')
                         .order_by('status', '-created_at')[:50])
    return render(request, 'cafeteria/package_order.html', {
        'packages': packages,
        'my_orders': my_orders,
        'levels': MeetingPackage.Level.choices,
        'catering_requests': catering_requests,
    })


@login_required
@require_POST
def catering_review(request, pk):
    """Facility Manager approves (optionally after modifying the package /
    attendees) or rejects a catering request that came with a room booking.
    On approval the order is placed to the cafeteria staff, paid by AJi."""
    if not is_facility_manager(request.user):
        raise PermissionDenied

    req = get_object_or_404(
        CateringRequest.objects.select_related('booking', 'booking__room', 'package', 'requested_by'),
        pk=pk, status=CateringRequest.Status.PENDING,
    )
    action = request.POST.get('action')
    comment = (request.POST.get('comment') or '')[:255]

    if action == 'reject':
        req.status = CateringRequest.Status.REJECTED
        req.reviewed_by = request.user
        req.review_comment = comment
        req.save(update_fields=['status', 'reviewed_by', 'review_comment', 'updated_at'])
        Notification.objects.create(
            recipient=req.requested_by, type=Notification.Type.WARNING,
            title='Catering request rejected',
            body=(f'Your catering for {req.booking.where} on {req.booking.date} was rejected by the '
                  f'Facility Manager.{" — " + comment if comment else ""} Your room booking is not affected.'),
            link=reverse('services:room_booking'),
        )
        messages.success(request, 'Catering request rejected. The requester has been notified.')
        return redirect('cafeteria:package_order')

    if action == 'approve':
        # The FM can modify the package and attendee count before approving.
        new_package = MeetingPackage.objects.filter(pk=request.POST.get('package'), is_active=True).first()
        if new_package:
            req.package = new_package
        try:
            attendees = int(request.POST.get('attendees_count', req.attendees_count))
            req.attendees_count = max(1, attendees)
        except (TypeError, ValueError):
            pass

        booking = req.booking
        order = PackageOrder.objects.create(
            package=req.package,
            ordered_by=request.user,
            meeting_title=booking.title or f'Meeting in {booking.where}',
            meeting_room=booking.where,
            meeting_date=booking.date,
            meeting_time=booking.start_time,
            attendees_count=req.attendees_count,
            notes=req.notes,
        )
        req.status = CateringRequest.Status.APPROVED
        req.reviewed_by = request.user
        req.review_comment = comment
        req.package_order = order
        req.save(update_fields=['status', 'reviewed_by', 'review_comment', 'package',
                                'attendees_count', 'package_order', 'updated_at'])

        _notify_staff(
            'New meeting package order (approved catering)',
            f'{req.package.get_level_display()} — {req.package.name} for "{order.meeting_title}" '
            f'in {booking.where}, {booking.date} {booking.start_time.strftime("%H:%M")} '
            f'({req.attendees_count} attendees). Paid by AJi.',
            reverse('cafeteria:staff_dashboard') + '?tab=package-orders',
        )
        Notification.objects.create(
            recipient=req.requested_by, type=Notification.Type.SUCCESS,
            title='Catering request approved',
            body=(f'"{req.package.name}" for your meeting in {booking.where} on {booking.date} was approved '
                  f'and sent to the cafeteria. Paid by AJi.{" — " + comment if comment else ""}'),
            link=reverse('services:room_booking'),
        )
        messages.success(request, f'Approved — "{req.package.name}" ordered from the cafeteria for {req.attendees_count} attendees.')
        return redirect('cafeteria:package_order')

    messages.error(request, 'Unknown action.')
    return redirect('cafeteria:package_order')
