# cafeteria/admin.py
from django.contrib import admin

from .models import (
    CafeteriaOrder, CafeteriaOrderItem, MenuCategory, MenuItem,
    MeetingPackage, PackageOrder,
)


class MenuItemInline(admin.TabularInline):
    model = MenuItem
    extra = 0


@admin.register(MenuCategory)
class MenuCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'order', 'is_active')
    inlines = [MenuItemInline]


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'price', 'is_active')
    list_filter = ('category', 'is_active')
    search_fields = ('name',)


class OrderItemInline(admin.TabularInline):
    model = CafeteriaOrderItem
    extra = 0


@admin.register(CafeteriaOrder)
class CafeteriaOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'ordered_by', 'status', 'created_at')
    list_filter = ('status',)
    inlines = [OrderItemInline]


@admin.register(MeetingPackage)
class MeetingPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'level', 'price_per_person', 'is_active')
    list_filter = ('level', 'is_active')


@admin.register(PackageOrder)
class PackageOrderAdmin(admin.ModelAdmin):
    list_display = ('package', 'meeting_title', 'meeting_date', 'attendees_count', 'status', 'ordered_by')
    list_filter = ('status', 'package__level')
