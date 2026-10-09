# services/urls.py
from django.urls import path

from . import fleet_views, views

app_name = 'services'

urlpatterns = [
    path('job-requisition/new/', views.job_requisition_new, name='job_requisition_new'),
    path('job-requisition/<int:pk>/', views.job_requisition_detail, name='job_requisition_detail'),
    path('job-requisition/<int:pk>/action/', views.job_requisition_action, name='job_requisition_action'),
    path('approvals/', views.approvals_inbox, name='approvals_inbox'),
    path('api/departments/', views.department_list_api, name='api_departments'),
    path('api/department-managers/', views.department_managers_api, name='api_department_managers'),
    path('room-booking/', views.room_booking, name='room_booking'),
    path('room-booking/requests/', views.room_requests, name='room_requests'),
    path('room-booking/requests/<int:pk>/review/', views.room_request_review, name='room_request_review'),
    path('room-booking/api/schedule/', views.room_schedule_api, name='room_schedule_api'),
    path('room-booking/api/week-schedule/', views.room_week_schedule_api, name='room_week_schedule_api'),
    path('room-booking/api/book/', views.room_booking_create, name='room_booking_create'),
    path('room-booking/api/update/<int:pk>/', views.room_booking_update, name='room_booking_update'),
    path('room-booking/api/cancel/<int:pk>/', views.room_booking_cancel, name='room_booking_cancel'),
    path('room-booking/api/invite/<int:pk>/respond/', views.meeting_invite_respond, name='meeting_invite_respond'),

    # ── the fleet ─────────────────────────────────────────────────────────
    path('car-booking/', fleet_views.car_booking, name='car_booking'),
    path('car-booking/api/availability/', fleet_views.car_availability_api,
         name='car_availability_api'),
    path('car-booking/api/book/', fleet_views.car_booking_create,
         name='car_booking_create'),
    path('car-booking/api/cancel/<int:pk>/', fleet_views.car_booking_cancel,
         name='car_booking_cancel'),
    path('car-booking/api/geocode/', fleet_views.geocode_api, name='geocode_api'),
    path('car-booking/api/tile/<int:z>/<int:x>/<int:y>.png',
         fleet_views.tile_proxy, name='map_tile'),

    # the Bookings desk
    path('car-booking/requests/', fleet_views.car_requests, name='car_requests'),
    path('car-booking/requests/<int:pk>/', fleet_views.car_request_detail,
         name='car_request_detail'),
    path('car-booking/requests/<int:pk>/review/', fleet_views.car_request_review,
         name='car_request_review'),
    path('car-booking/schedule/', fleet_views.car_schedule, name='car_schedule'),
    path('car-booking/log/', fleet_views.car_log, name='car_log'),
    path('car-booking/log/export/', fleet_views.car_log_export, name='car_log_export'),

    # the fleet console
    path('fleet/', fleet_views.fleet_console, name='fleet_console'),
    path('fleet/approvers/', fleet_views.fleet_approvers, name='fleet_approvers'),
    path('fleet/approvers/save/', fleet_views.fleet_approver_save,
         name='fleet_approver_save'),
    path('fleet/api/people/', fleet_views.fleet_people_api, name='fleet_people_api'),
    path('car-booking/requests/<int:pk>/trip/', fleet_views.car_trip_save,
         name='car_trip_save'),
    path('fleet/car/<int:pk>/', fleet_views.fleet_vehicle, name='fleet_vehicle'),
    path('fleet/car/new/save/', fleet_views.fleet_vehicle_save, name='fleet_vehicle_new'),
    path('fleet/car/<int:pk>/save/', fleet_views.fleet_vehicle_save,
         name='fleet_vehicle_save'),
    path('fleet/car/<int:pk>/retire/', fleet_views.fleet_vehicle_retire,
         name='fleet_vehicle_retire'),
    path('fleet/event/new/save/', fleet_views.fleet_event_save, name='fleet_event_new'),
    path('fleet/event/<int:pk>/save/', fleet_views.fleet_event_save,
         name='fleet_event_save'),
    path('fleet/event/<int:pk>/delete/', fleet_views.fleet_event_delete,
         name='fleet_event_delete'),
    path('fleet/trip/new/save/', fleet_views.fleet_trip_log_save, name='fleet_trip_new'),
    path('fleet/trip/<int:pk>/save/', fleet_views.fleet_trip_log_save,
         name='fleet_trip_save'),
]
