# core/management/commands/delete_dotted_users.py

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

User = get_user_model()


class Command(BaseCommand):
    help = "Delete users whose username contains more than one dot (.)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only list the users that would be deleted, without deleting them.",
        )
        parser.add_argument(
            "--keep-superusers",
            action="store_true",
            help="Skip superuser accounts even if their username matches.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        keep_superusers = options["keep_superusers"]

        # Find users with more than one dot in the username
        users = [
            u for u in User.objects.all()
            if u.username.count(".") > 1
        ]

        if keep_superusers:
            users = [u for u in users if not u.is_superuser]

        if not users:
            self.stdout.write(self.style.SUCCESS("No users found with more than one dot in their username."))
            return

        self.stdout.write(f"Found {len(users)} user(s) with more than one dot in their username:")
        for u in users:
            self.stdout.write(f"  - {u.username} (id={u.pk}, superuser={u.is_superuser})")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: no users were deleted."))
            return

        ids = [u.pk for u in users]
        deleted_count, _ = User.objects.filter(pk__in=ids).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {len(ids)} user(s) ({deleted_count} total objects removed)."))