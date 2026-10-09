# cafeteria/management/commands/seed_cafeteria.py
"""Seeds the AJi cafeteria: menu categories & items (drinks and snacks taken
from the Blue Fig menu — alcoholic sections excluded), the meeting packages
for AJi meetings, the 'Cafeteria Staff' and 'Facility Managers' groups, and
the two services under Workplace & Facilities on My AJi."""
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from cafeteria.models import (
    MeetingPackage, MenuCategory, MenuItem, FACILITY_GROUP, STAFF_GROUP,
)
from services.models import Service, ServiceCategory

# (category, order, [(name, description, price)])
MENU = [
    ('Coffee — Gourmet Roast', 0, [
        ('Gourmet Roast Original', 'Regular size', '3.75'),
        ('Gourmet Roast Flavored', 'Regular size', '4.25'),
        ('Cappuccino Original', 'Regular size', '4.00'),
        ('Cappuccino Flavored', 'Regular size', '4.25'),
        ('Latte Original', 'Regular size', '4.00'),
        ('Latte Flavored', 'Regular size', '4.25'),
        ('Spanish Latte', 'Regular size', '4.50'),
        ('Flat White', '', '3.85'),
        ('Hot Chocolate', 'Original', '4.25'),
    ]),
    ('Espresso Bar', 1, [
        ('Espresso', 'Single shot', '3.00'),
        ('Americano', 'Espresso topped with hot water', '3.00'),
        ('Espresso Romano', 'Espresso with a slice of lemon', '3.00'),
        ('Espresso Macchiato', 'Espresso topped with milk foam', '3.00'),
        ('Espresso Panna', 'Espresso topped with whipped cream', '3.00'),
        ('Ristretto', 'Concentrated espresso shot', '3.85'),
        ('Mochaccino', 'Espresso shot with melted chocolate & milk', '4.50'),
        ('Affogato', 'Vanilla ice cream and two hot shots of espresso', '4.25'),
    ]),
    ('Iced Coffee', 2, [
        ('Iced Cappuccino', 'Original, regular size', '4.00'),
        ('Iced Latte', 'Original, regular size', '4.00'),
        ('Frappe', 'Original, regular size', '4.75'),
        ('Iced Spanish Latte', '', '4.50'),
        ('Iced Coffee', 'Regular size', '3.75'),
        ('V60 Iced Coffee', '', '4.00'),
        ('Cold Brew', '', '4.00'),
    ]),
    ('Tea & Herbal Infusions', 3, [
        ('English Breakfast Tea', '', '2.75'),
        ('Earl Grey Tea', '', '2.75'),
        ('Peppermint Tea', '', '2.75'),
        ('Chamomile', '', '2.75'),
        ('Green Tea with Mint', '', '2.75'),
        ('Lemon with Ginger', '', '2.75'),
        ('Ginger & Lemon with Honey', '', '3.00'),
        ('Matcha Green Tea', '', '4.50'),
        ('Hot Hibiscus Tea', '', '2.75'),
    ]),
    ('Fresh Juices & Detox', 4, [
        ('Orange Juice', 'Freshly squeezed', '4.50'),
        ('Apple Juice', 'Freshly squeezed', '4.50'),
        ('Carrot Juice', 'Freshly squeezed', '4.50'),
        ('Green Glow', 'Cucumber, pear, kale, parsley, green apple & rocca', '4.50'),
        ('Green Detox', 'Green apple, spinach, cucumber, ginger, lemon & celery', '4.50'),
        ('Vitamin Power', 'Orange, carrots & apple', '4.25'),
        ('Super Energy', 'Pineapple juice, banana & cranberry juice', '4.25'),
        ('Mango Sherbet', 'Mango, lemon juice, brown sugar and fresh mint', '4.25'),
        ('Beetroot Bliss', 'Beetroot, green apple, orange juice & orange', '4.25'),
    ]),
    ('Smoothies & Milkshakes', 5, [
        ('White Strawberry Smoothie', 'Strawberry, banana, milk & strawberry syrup', '4.00'),
        ('Avocolada', 'Avocado, milk, banana, vanilla ice cream', '5.25'),
        ('Lemon Basil Spritz', 'Soda, basil, fresh lemon & syrup', '4.25'),
        ('Very Berry', 'Strawberry, raspberry & cranberry juice', '5.00'),
        ('Sour Breeze', 'Fresh lemon juice, sugar & mint', '4.00'),
        ('Cucumber Cool', 'Cucumber, lemon & mint', '3.60'),
        ('Tropical Island', 'Mango, strawberry, mango juice and strawberry syrup', '4.00'),
        ('Strawberry Milkshake', '', '4.75'),
        ('Chocolate Milkshake', '', '4.75'),
        ('Vanilla Milkshake', '', '4.75'),
        ('Oreo Cookies Milkshake', '', '4.75'),
    ]),
    ('Cold Drinks & Water', 6, [
        ('Blue Fig Soda', 'Regular size', '3.00'),
        ('Iced Tea', 'Regular size', '3.50'),
        ('Cold Hibiscus Tea', 'Original, regular size', '2.75'),
        ('Mineral Water — Small', '', '1.75'),
        ('Mineral Water — Large', '', '2.75'),
        ('Sparkling Water', 'Perrier', '3.25'),
        ('Pepsi', '', '2.75'),
        ('Soda Water', '', '2.75'),
        ('Red Bull', '', '4.75'),
    ]),
    ('Cookies & Muffins', 7, [
        ('White Chocolate Chip Cookie', '', '2.00'),
        ('Dark Chocolate Chip Cookie', '', '2.00'),
        ('Oatmeal Cookie', '', '2.00'),
        ('Peanut Butter Cookie', 'Contains nuts', '2.00'),
        ('Mixed Berries Muffin', '', '2.75'),
        ('Chocolate Muffin', '', '2.75'),
        ('Carrot Muffin', '', '2.75'),
        ('Date Muffin', '', '2.75'),
    ]),
    ('Sweet Bites', 8, [
        ('Carrot Cake', 'Slice', '4.25'),
        ('Chocolate Cake', 'Slice', '4.25'),
        ('Brownie', '', '5.50'),
        ('Pistachio Cake', 'Slice', '5.50'),
        ('New York Baked Cheesecake', 'Served with fruit compote', '4.25'),
        ('Dutch Apple Crumble', 'House-made apple crumble', '4.25'),
    ]),
    ('Snacks & Light Bites', 9, [
        ('Hummus Toast', 'Hummus on sourdough, mixed seeds, cherry tomatoes & olive oil', '3.50'),
        ('Avocado Toast', 'Sourdough with labaneh, avocado mix & seeds', '6.00'),
        ('Granola & Flavored Yoghurt', '', '5.50'),
        ('Zaater Manqousheh', 'Thyme on your choice of dough', '3.85'),
        ('Cheese Manqousheh', '', '4.65'),
        ('Cheese & Zaater Manqousheh', '', '4.65'),
        ('French Fries', '', '4.25'),
        ('Curly Fries', '', '4.50'),
        ('Edamame', 'Roasted edamame with grated parmesan & soy dip', '6.25'),
        ('Turkey Wrap', 'Smoked turkey, lettuce, cucumber, avocado on bran shrak bread', '7.50'),
        ('Chicken Caesar Sandwich', 'Grilled chicken breast on house-made ciabatta', '8.00'),
    ]),
]

# (name, level, description, price_per_person, order)
PACKAGES = [
    ('VIP Meeting Package', MeetingPackage.Level.VIP,
     'Premium spread for VIP guests: specialty coffee (Spanish latte, V60, espresso bar), '
     'matcha & fine teas, fresh detox juices, San Sebastian cheesecake bites, pistachio cake, '
     'fruit platter and mixed nuts. White-glove service to the meeting room.', '18.00', 0),
    ('Refreshment Package', MeetingPackage.Level.REFRESHMENT,
     'Coffee & tea station, fresh orange juice, mineral water, assorted cookies and mini muffins.',
     '7.50', 1),
    ('Drinks Package', MeetingPackage.Level.DRINKS,
     'Hot drinks (coffee, tea) and cold drinks (soft drinks, water, iced tea) only.', '4.50', 2),
    ('Breakfast Package', MeetingPackage.Level.BREAKFAST,
     'Morning spread: manqousheh selection (zaater, cheese), hummus & avocado toasts, '
     'granola & yoghurt cups, fruit salad, fresh juices, coffee & tea.', '12.00', 3),
    ('Lunch Package', MeetingPackage.Level.LUNCH,
     'Lunch spread: sandwich & wrap platters (turkey, chicken caesar), salads, '
     'fries & sides, dessert bites, soft drinks, coffee & tea.', '15.00', 4),
]


class Command(BaseCommand):
    help = 'Seeds the AJi cafeteria menu (from the Blue Fig menu), meeting packages, groups, and My AJi services.'

    # ── seed photos ──────────────────────────────────────────────────────
    # (background, accent) per category + icon key. Drawn locally with PIL so
    # the photos work on the intranet without internet access.
    CATEGORY_STYLE = {
        'Coffee — Gourmet Roast':   ((62, 39, 35),   (212, 163, 115), 'cup'),
        'Espresso Bar':             ((33, 24, 22),   (188, 132, 91),  'cup'),
        'Iced Coffee':              ((46, 64, 82),   (168, 202, 225), 'glass'),
        'Tea & Herbal Infusions':   ((52, 78, 65),   (183, 216, 185), 'teapot'),
        'Fresh Juices & Detox':     ((219, 122, 47), (255, 220, 168), 'glass'),
        'Smoothies & Milkshakes':   ((156, 71, 123), (243, 200, 224), 'shake'),
        'Cold Drinks & Water':      ((41, 94, 133),  (176, 220, 245), 'bottle'),
        'Cookies & Muffins':        ((150, 92, 44),  (240, 205, 160), 'cookie'),
        'Sweet Bites':              ((122, 62, 72),  (240, 190, 200), 'cake'),
        'Snacks & Light Bites':     ((104, 116, 50), (222, 230, 170), 'plate'),
    }

    def _draw_icon(self, draw, icon, cx, cy, s, color):
        """Draw a simple food/drink icon centered at (cx, cy) with size s."""
        w = max(3, s // 14)
        if icon == 'cup':
            draw.rounded_rectangle([cx - s//2, cy - s//3, cx + s//3, cy + s//2], radius=s//10, outline=color, width=w)
            draw.arc([cx + s//4, cy - s//5, cx + s//1.5, cy + s//4], start=-90, end=90, fill=color, width=w)
            for dx in (-s//5, 0, s//5):
                draw.arc([cx + dx - s//10, cy - s//1.4, cx + dx + s//10, cy - s//2.5], start=90, end=270, fill=color, width=w)
        elif icon == 'glass':
            draw.polygon([(cx - s//2.2, cy - s//2), (cx + s//2.2, cy - s//2), (cx + s//3.2, cy + s//2), (cx - s//3.2, cy + s//2)], outline=color, width=w)
            draw.line([cx + s//8, cy - s//1.6, cx + s//2.8, cy - s//2], fill=color, width=w)  # straw
            draw.line([cx - s//2.6, cy - s//8, cx + s//2.6, cy - s//8], fill=color, width=w)  # fill level
        elif icon == 'teapot':
            draw.ellipse([cx - s//2, cy - s//4, cx + s//3, cy + s//2], outline=color, width=w)
            draw.arc([cx + s//5, cy - s//6, cx + s//1.5, cy + s//5], start=-70, end=110, fill=color, width=w)
            draw.arc([cx - s//4, cy - s//2, cx + s//8, cy - s//8], start=140, end=400, fill=color, width=w)  # lid
        elif icon == 'shake':
            draw.polygon([(cx - s//2.4, cy - s//3), (cx + s//2.4, cy - s//3), (cx + s//3.4, cy + s//2), (cx - s//3.4, cy + s//2)], outline=color, width=w)
            draw.ellipse([cx - s//3.2, cy - s//1.9, cx + s//3.2, cy - s//4.5], outline=color, width=w)  # cream
            draw.line([cx, cy - s//1.7, cx + s//4, cy - s//2.4], fill=color, width=w)
        elif icon == 'bottle':
            draw.rounded_rectangle([cx - s//4, cy - s//3, cx + s//4, cy + s//2], radius=s//8, outline=color, width=w)
            draw.rectangle([cx - s//9, cy - s//1.8, cx + s//9, cy - s//3], outline=color, width=w)
            draw.line([cx - s//4, cy, cx + s//4, cy], fill=color, width=w)
        elif icon == 'cookie':
            draw.ellipse([cx - s//2, cy - s//2, cx + s//2, cy + s//2], outline=color, width=w)
            for dx, dy in ((-s//5, -s//6), (s//6, -s//4), (s//4, s//6), (-s//7, s//5), (0, 0)):
                r = s // 14
                draw.ellipse([cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r], fill=color)
        elif icon == 'cake':
            draw.rounded_rectangle([cx - s//2, cy - s//8, cx + s//2, cy + s//2], radius=s//12, outline=color, width=w)
            draw.arc([cx - s//2, cy - s//2, cx + s//2, cy + s//4], start=180, end=360, fill=color, width=w)
            r = s // 12
            draw.ellipse([cx - r, cy - s//1.9 - r, cx + r, cy - s//1.9 + r], fill=color)  # cherry
        else:  # plate
            draw.ellipse([cx - s//1.8, cy - s//3.2, cx + s//1.8, cy + s//3.2], outline=color, width=w)
            draw.ellipse([cx - s//3, cy - s//5.5, cx + s//3, cy + s//5.5], outline=color, width=w)

    def _make_item_photo(self, item, category_name):
        from PIL import Image, ImageDraw, ImageFont
        bg, accent, icon = self.CATEGORY_STYLE.get(category_name, ((70, 70, 70), (220, 220, 220), 'plate'))
        W, H = 480, 360
        img = Image.new('RGB', (W, H), bg)
        draw = ImageDraw.Draw(img)

        # soft vertical shade + accent corner wedge for a bit of depth
        top = tuple(min(255, c + 22) for c in bg)
        for y in range(H):
            t = y / H
            row = tuple(int(top[i] * (1 - t) + bg[i] * t) for i in range(3))
            draw.line([(0, y), (W, y)], fill=row)
        wedge = tuple(int((bg[i] + accent[i]) / 2) for i in range(3))
        draw.polygon([(W, 0), (W - 150, 0), (W, 110)], fill=wedge)

        # icon medallion
        cx, cy, s = W // 2, H // 2 - 30, 130
        draw.ellipse([cx - 95, cy - 95, cx + 95, cy + 95], outline=accent, width=3)
        self._draw_icon(draw, icon, cx, cy, s, accent)

        # item name
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 26)
            small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 15)
        except OSError:
            font = small = ImageFont.load_default()
        name = item.name if len(item.name) <= 30 else item.name[:28] + '…'
        tw = draw.textlength(name, font=font)
        draw.text(((W - tw) / 2, H - 82), name, font=font, fill=(250, 246, 238))
        cat = category_name.upper()
        cw = draw.textlength(cat, font=small)
        draw.text(((W - cw) / 2, H - 44), cat, font=small, fill=accent)

        buf = BytesIO()
        img.save(buf, format='JPEG', quality=88)
        return ContentFile(buf.getvalue())

    def handle(self, *args, **options):
        # Groups
        staff_group, _ = Group.objects.get_or_create(name=STAFF_GROUP)
        facility_group, _ = Group.objects.get_or_create(name=FACILITY_GROUP)
        from services.models import RECEPTION_GROUP
        Group.objects.get_or_create(name=RECEPTION_GROUP)
        self.stdout.write(f'Groups ready: "{STAFF_GROUP}", "{FACILITY_GROUP}", "{RECEPTION_GROUP}"')

        # Menu
        created_items = 0
        photos_added = 0
        for cat_name, order, items in MENU:
            cat, _ = MenuCategory.objects.get_or_create(name=cat_name, defaults={'order': order})
            for i, (name, description, price) in enumerate(items):
                item, created = MenuItem.objects.get_or_create(
                    category=cat, name=name,
                    defaults={'description': description, 'price': Decimal(price), 'order': i},
                )
                created_items += int(created)
                if not item.image:
                    photo = self._make_item_photo(item, cat_name)
                    item.image.save(f'{cat.pk}-{item.pk}.jpg', photo, save=True)
                    photos_added += 1
        self.stdout.write(f'Menu seeded: {MenuCategory.objects.count()} categories, '
                          f'{MenuItem.objects.count()} items ({created_items} new, {photos_added} photos generated)')

        # Meeting packages
        for name, level, description, price, order in PACKAGES:
            MeetingPackage.objects.get_or_create(
                name=name,
                defaults={'level': level, 'description': description,
                          'price_per_person': Decimal(price), 'order': order},
            )
        self.stdout.write(f'Meeting packages seeded: {MeetingPackage.objects.count()}')

        # My AJi services under Workplace & Facilities
        facilities, _ = ServiceCategory.objects.get_or_create(
            name='Workplace & Facilities',
            defaults={'category_type': ServiceCategory.CategoryType.SERVICES,
                      'icon': 'facilities', 'order': 4},
        )
        Service.objects.get_or_create(
            service_key='cafeteria',
            defaults={'category': facilities, 'name': 'Cafeteria',
                      'description': 'Order drinks & snacks from the AJi cafeteria',
                      'group': None, 'order': 5},
        )
        Service.objects.get_or_create(
            service_key='meeting_packages',
            defaults={'category': facilities, 'name': 'Meeting Packages',
                      'description': 'Order catering packages for AJi meetings (paid by AJi)',
                      'group': facility_group, 'order': 6},
        )
        self.stdout.write(self.style.SUCCESS(
            '\nDone. "Cafeteria" (all employees) and "Meeting Packages" (Facility Managers) '
            'are live under Workplace & Facilities. Add users to the "Cafeteria Staff" group '
            'to give them the staff dashboard at /cafeteria/staff/.'
        ))
