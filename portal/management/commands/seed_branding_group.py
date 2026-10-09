# portal/management/commands/seed_branding_group.py
"""
python manage.py seed_branding_group
Puts the 'Branding' group back to exactly the brand and Booth permissions.

Run this if the group has been edited by hand. It uses .set(), so it removes
anything that does not belong as well as adding what does — which is the point:
the group was found in August 2026 holding 264 permissions, an identical set to
Intranet Admin, covering cafeteria, people, services, messaging and the auth
tables. Somebody had copied the administrator group rather than building this
one. Anybody given Branding to change a logo was receiving the ability to edit
users and delete records across the whole Hub.

Worth knowing: the Brand Files and Booth screens gate on *membership* of this
group, not on these permissions -- see core.user_can_manage_branding. The
permissions matter in the Django admin and to any code that asks has_perm(),
which is why an over-broad set was a real exposure rather than a tidiness
problem.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Reset the Branding permission group to brand and Booth models only'

    def handle(self, *args, **options):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType
        from portal.models import (BoothAlbum, BoothPhoto, BoothVideo, BrandAsset,
                                   BrandAssetCategory, BrandColour, BrandFileGroup,
                                   BrandSection, BrandTemplate, BrandTemplateFile)

        group, created = Group.objects.get_or_create(name='Branding')
        self.stdout.write(f'  {"Created" if created else "Found"} group: Branding')
        before = group.permissions.count()

        # Every model the Brand Files and AJi Booth screens write to. The list
        # was five models and missed BrandFileGroup, BrandAssetCategory,
        # BrandAsset, BoothPhoto and BoothVideo, so a reset would have taken
        # away permissions the module genuinely needs.
        brand_models = [BrandSection, BrandTemplate, BrandTemplateFile, BrandFileGroup,
                        BrandColour, BrandAssetCategory, BrandAsset,
                        BoothAlbum, BoothPhoto, BoothVideo]
        perms = []
        for model in brand_models:
            ct = ContentType.objects.get_for_model(model)
            for action in ['add', 'change', 'delete', 'view']:
                codename = f'{action}_{model.__name__.lower()}'
                try:
                    perms.append(Permission.objects.get(content_type=ct, codename=codename))
                except Permission.DoesNotExist:
                    self.stdout.write(
                        self.style.WARNING(f'  Permission not found: {codename} — run migrate first')
                    )

        group.permissions.set(perms)
        removed = before - len(perms)
        self.stdout.write(
            self.style.SUCCESS(
                f'\nBranding now has {len(perms)} permissions (was {before}'
                f'{f", {removed} removed" if removed > 0 else ""}).\n'
                'Members of the group are unchanged.'
            )
        )
