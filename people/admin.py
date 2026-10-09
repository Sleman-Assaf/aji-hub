# people/admin.py
from django.contrib import admin
from .models import EmpHR, Entity, Department, Section, Employee, SkillCategory, Skill, EmployeeSkill


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'entity', 'manager')


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display  = ('name', 'department', 'head')
    list_filter   = ('department__entity',)
    search_fields = ('name',)
    ordering      = ('department__name', 'name')


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    # The record id opens the employee's detail page — a stable handle you can
    # read straight off the list, quote in a ticket, and click. The name stays
    # a link too, since that is what fingers reach for.
    list_display   = ('id', 'emp_code', 'full_name', 'role', 'entity', 'department', 'extension', 'office', 'is_active')
    list_display_links = ('id', 'full_name')
    list_filter    = ('entity', 'is_active')
    search_fields  = ('id', 'full_name', 'role', 'emp_code', 'extension', 'mobile_number')
    raw_id_fields  = ('manager', 'user')
    readonly_fields = ('last_seen',)
    fieldsets = (
        (None, {'fields': ('user', 'emp_code', 'full_name', 'role', 'is_active')}),
        ('Organisation', {'fields': ('entity', 'department', 'section', 'manager')}),
        ('Contact', {'fields': ('email', 'mobile_country_code', 'mobile_number', 'extension', 'office', 'linkedin')}),
        ('Profile', {'fields': ('photo', 'cv', 'bio', 'birthday', 'country')}),
        ('Meta', {'fields': ('last_seen',)}),
    )


@admin.register(SkillCategory)
class SkillCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'order')


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ('name', 'category')
    list_filter  = ('category',)


@admin.register(EmployeeSkill)
class EmployeeSkillAdmin(admin.ModelAdmin):
    list_display  = ('employee', 'skill', 'level')
    raw_id_fields = ('employee',)


@admin.register(EmpHR)
class EmpHRAdmin(admin.ModelAdmin):
    """HR's own list, read-mostly: the importer owns this table."""
    list_display  = ('id', 'emp_code', 'full_name', 'entity', 'department',
                     'section', 'position', 'manager', 'email', 'country')
    list_display_links = ('id', 'full_name')
    list_filter   = ('entity', 'country', 'department')
    search_fields = ('full_name', 'emp_code', 'email', 'position', 'manager_name')
    raw_id_fields = ('manager', 'department', 'section')
