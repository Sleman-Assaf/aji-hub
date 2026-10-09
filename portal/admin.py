from django.contrib import admin
from .models import Policy, PolicyCategory, BrandAssetCategory, BrandAsset, OnboardingSection, OnboardingItem, OnboardingHandbookItem

@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'updated_date')

@admin.register(BrandAssetCategory)
class BrandAssetCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'order')

@admin.register(BrandAsset)
class BrandAssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'category')

class OnboardingItemInline(admin.TabularInline):
    model = OnboardingItem
    extra = 1

@admin.register(OnboardingSection)
class OnboardingSectionAdmin(admin.ModelAdmin):
    list_display = ('tab_key', 'title')
    inlines = [OnboardingItemInline]


@admin.register(PolicyCategory)
class PolicyCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'order')
    ordering = ('order', 'name')


# ── Brand Templates ──────────────────────────────────────────────────────────

from .models import BrandSection, BrandTemplate, BrandTemplateFile, BrandFileGroup


class BrandFileGroupInline(admin.TabularInline):
    model   = BrandFileGroup
    extra   = 1
    fields  = ('name', 'order')


class BrandTemplateFileInline(admin.TabularInline):
    model   = BrandTemplateFile
    extra   = 1
    fields  = ('group', 'label', 'file', 'version')


@admin.register(BrandTemplate)
class BrandTemplateAdmin(admin.ModelAdmin):
    list_display  = ('name', 'section', 'description', 'file_count', 'order')
    list_filter   = ('section',)
    ordering      = ('section__order', 'order', 'name')
    inlines       = [BrandFileGroupInline, BrandTemplateFileInline]


@admin.register(BrandSection)
class BrandSectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'section_type', 'order')
    ordering     = ('order',)


@admin.register(BrandTemplateFile)
class BrandTemplateFileAdmin(admin.ModelAdmin):
    list_display = ('label', 'template', 'version', 'updated_at')
    list_filter  = ('template__section',)


# ── Colours & Booth ──────────────────────────────────────────────────────────

from .models import BrandColour, BoothAlbum, BoothPhoto, BoothVideo


@admin.register(BrandColour)
class BrandColourAdmin(admin.ModelAdmin):
    list_display = ('name', 'hex', 'ref', 'light', 'order')
    ordering     = ('order',)


@admin.register(BoothAlbum)
class BoothAlbumAdmin(admin.ModelAdmin):
    list_display = ('name', 'tag', 'period', 'photo_count', 'order')
    ordering     = ('order',)


class BoothPhotoInline(admin.TabularInline):
    model  = BoothPhoto
    extra  = 1
    fields = ('image', 'caption')


class BoothVideoInline(admin.TabularInline):
    model  = BoothVideo
    extra  = 1
    fields = ('video', 'caption', 'uploaded_by')

# Re-register BoothAlbum with inlines
admin.site.unregister(BoothAlbum)

@admin.register(BoothAlbum)
class BoothAlbumAdmin2(admin.ModelAdmin):
    list_display = ('name', 'tag', 'period', 'photo_count', 'order')
    ordering     = ('order',)
    inlines      = [BoothPhotoInline, BoothVideoInline]


# ── HR-managed content ──────────────────────────────────────────────────────

from .models import OnboardingHandbookItem, WellnessProgram, ChildcarePartner, GrowthCertification


@admin.register(OnboardingHandbookItem)
class OnboardingHandbookItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'tab', 'order')
    list_filter  = ('tab',)
    ordering     = ('tab', 'order')


@admin.register(WellnessProgram)
class WellnessProgramAdmin(admin.ModelAdmin):
    list_display = ('name', 'period', 'order')
    ordering     = ('order',)


@admin.register(ChildcarePartner)
class ChildcarePartnerAdmin(admin.ModelAdmin):
    list_display = ('name', 'order')
    ordering     = ('order',)


@admin.register(GrowthCertification)
class GrowthCertificationAdmin(admin.ModelAdmin):
    list_display = ('name', 'order')
    ordering     = ('order',)


# ── CEO Letter & Handbook ────────────────────────────────────────────────────

from .models import CEOLetter, EmployeeHandbookFile


@admin.register(CEOLetter)
class CEOLetterAdmin(admin.ModelAdmin):
    list_display = ('name', 'title', 'updated_at')


@admin.register(EmployeeHandbookFile)
class EmployeeHandbookFileAdmin(admin.ModelAdmin):
    list_display = ('title', 'updated_at')
