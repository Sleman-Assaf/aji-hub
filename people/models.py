# people/models.py
from django.db import models
from django.contrib.auth.models import User
import os
import re


def emp_photo_path(instance, filename):
    code = getattr(instance, 'emp_code', None) or str(instance.pk or 'unknown')
    ext  = os.path.splitext(filename)[1]
    name = re.sub(r'[^a-zA-Z0-9_-]', '', os.path.splitext(filename)[0])[:40]
    return f'employees/{code}/photos/{name}{ext}'


def emp_cv_path(instance, filename):
    code = getattr(instance, 'emp_code', None) or str(instance.pk or 'unknown')
    ext  = os.path.splitext(filename)[1]
    name = re.sub(r'[^a-zA-Z0-9_-]', '', os.path.splitext(filename)[0])[:40]
    return f'employees/{code}/cvs/{name}{ext}'


def _emp_media_path(subfolder):
    """Shim kept for old migrations that reference this name."""
    if subfolder == 'photos':
        return emp_photo_path
    return emp_cv_path


class Entity(models.Model):
    code       = models.CharField(max_length=30, unique=True)
    name       = models.CharField(max_length=200)
    short_name = models.CharField(max_length=60, blank=True)

    class Meta:
        verbose_name_plural = 'Entities'
        ordering = ['name']

    def __str__(self):
        return self.name


class Department(models.Model):
    name    = models.CharField(max_length=200)
    entity  = models.ForeignKey(Entity, on_delete=models.SET_NULL, null=True, blank=True)
    manager = models.ForeignKey(
        'Employee', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='managed_departments',
        help_text='Employee who manages this department',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Section(models.Model):
    name       = models.CharField(max_length=200)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='sections')
    head       = models.ForeignKey(
        'Employee', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='headed_sections',
        help_text='Employee who heads this section',
    )

    class Meta:
        ordering = ['name']
        unique_together = ('name', 'department')

    def __str__(self):
        return f'{self.department.name} / {self.name}'


class Employee(models.Model):
    user      = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='employee')
    emp_code  = models.CharField(max_length=30, blank=True, db_index=True,
                                 help_text='Unique employee code from HR system (e.g. 8001)')
    full_name = models.CharField(max_length=200)
    role      = models.CharField(max_length=200, blank=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    section    = models.ForeignKey('Section', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='employees')
    entity    = models.ForeignKey(Entity, on_delete=models.SET_NULL, null=True, blank=True)
    manager   = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='direct_reports')
    email     = models.EmailField(blank=True)
    mobile_country_code = models.CharField(max_length=5, blank=True,
                                           help_text='International dialing code without +, e.g. 962')
    mobile_number = models.CharField(max_length=20, blank=True,
                                     help_text='Mobile number without the country code, e.g. 790123456')
    extension = models.CharField(max_length=20, blank=True, help_text='Internal phone extension')
    office    = models.CharField(max_length=100, blank=True, help_text='Office / site (e.g. AJi Jordan)')
    linkedin  = models.URLField(blank=True)
    birthday  = models.CharField(max_length=5, blank=True, help_text='MM-DD format')
    photo     = models.ImageField(upload_to=emp_photo_path, blank=True, null=True)
    cv        = models.FileField(upload_to=emp_cv_path, blank=True, null=True,
                                 help_text='PDF or Word CV')
    bio       = models.TextField(blank=True)
    last_seen = models.DateTimeField(null=True, blank=True, db_index=True)
    country   = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['full_name']

    def __str__(self):
        return self.full_name

    def initials(self):
        parts = self.full_name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return self.full_name[:2].upper()

    def full_mobile(self):
        """International mobile number (+CCXXXXXXXXX), or '' if not set."""
        if not self.mobile_number:
            return ''
        number = self.mobile_number.replace(' ', '')
        if self.mobile_country_code:
            return f"+{self.mobile_country_code.lstrip('+')}{number.lstrip('0')}"
        return number if number.startswith('+') else f'+{number}'

    def is_online(self):
        if not self.last_seen:
            return False
        from django.utils import timezone
        from datetime import timedelta
        return timezone.now() - self.last_seen < timedelta(minutes=3)

    def email_auto(self):
        if self.email:
            return self.email
        name = self.full_name.lower().replace(' ', '.').replace("'", '')
        return f'{name}@ajigroup.com'


class SkillCategory(models.Model):
    name  = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Skill Categories'

    def __str__(self):
        return self.name


class Skill(models.Model):
    category = models.ForeignKey(SkillCategory, on_delete=models.CASCADE, related_name='skills')
    name     = models.CharField(max_length=200)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.category})'


class EmployeeSkill(models.Model):
    class Level(models.IntegerChoices):
        BEGINNER     = 1, 'Beginner'
        INTERMEDIATE = 2, 'Intermediate'
        ADVANCED     = 3, 'Proficient'
        EXPERT       = 4, 'Expert'

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='skills')
    skill    = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='employees')
    level    = models.PositiveSmallIntegerField(choices=Level.choices, default=Level.INTERMEDIATE)

    class Meta:
        unique_together = ('employee', 'skill')

    def __str__(self):
        return f'{self.employee} — {self.skill.name}'


class EmpHR(models.Model):
    """The HR master list, one row per person — linked into the org graph.

    Imported from HR's consolidated Excel (manage.py import_hr_excel). Not
    text columns but real foreign keys: the entity, department and section a
    row names are resolved to (or created as) the same Entity / Department /
    Section rows the Employee table uses, and the manager to the EmpHR row it
    points at — so feeding an Employee is assignment, not string matching.

    `email` is the unique index between systems: taken from the sheet's
    Employee Email column when present (normalized to @ajigroup.com), derived
    from the name (First_Last@ajigroup.com — the same rule logins follow)
    when absent, and left empty only when two people would collide on it.
    The AD sync derives each login's address the same way on the fly, and
    the feed joins the two on that address.
    """
    email        = models.EmailField(unique=True, null=True, blank=True,
                                     help_text='The unique link to the AD-side account.')
    entity       = models.ForeignKey(Entity, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='hr_rows')
    location     = models.CharField(max_length=100, blank=True)
    emp_code     = models.CharField(max_length=30, db_index=True)
    full_name    = models.CharField(max_length=200)
    department   = models.ForeignKey(Department, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='hr_rows')
    section      = models.ForeignKey(Section, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='hr_rows')
    division     = models.CharField(max_length=200, blank=True)
    site_project = models.CharField(max_length=200, blank=True)
    position     = models.CharField(max_length=200, blank=True)
    birth_date   = models.DateField(null=True, blank=True)
    country      = models.CharField(max_length=100, blank=True)
    manager      = models.ForeignKey('self', on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='reports')
    manager_code = models.CharField(max_length=30, blank=True)
    manager_name = models.CharField(max_length=200, blank=True)
    imported_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'HR employee record'
        verbose_name_plural = 'HR employee records'
        # Codes repeat across entities (AJiBE 170 vs HDP 170); together they
        # are the sheet's real identity.
        unique_together = ('entity', 'emp_code')
        ordering = ['full_name']

    def __str__(self):
        return f'{self.full_name} ({self.entity_id and self.entity.code or "?"} {self.emp_code})'
