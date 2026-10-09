# cms/icons_ui.py
"""Navigation and interface icon shapes.

These were inline SVG in base.html and a JavaScript object literal, which meant
they could not be edited without touching code. They live here as the *fallback*
shapes: the editable library in cms.Icon overrides them, and if a row is deleted
or blanked the site falls back to these, so navigation and buttons can never
render empty.
"""

#: Navigation icons — previously the ICONS object literal in base.html.
NAV_ICONS = {
    'heart':  '<path d="M19 14c1.5-1.5 3-3.3 3-5.5A4.5 4.5 0 0 0 12 5 4.5 4.5 0 0 0 2 8.5c0 2.2 1.5 4 3 5.5l7 7z"/>',
    'book-open': '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/>',
    'idcard': '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/><path d="M5.5 16c.6-1.4 1.9-2 3.5-2s2.9.6 3.5 2M15 9.5h4M15 13.5h3"/>',
    'spark':  '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"/>',
    'org':    '<rect x="9" y="2" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="16" y="16" width="6" height="6" rx="1"/><path d="M12 8v4M5 16v-2h14v2M12 12v2"/>',
    'chat':   '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    'camera': '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
}

#: Interface icons — buttons, chrome, and the small marks inside components.
UI_ICONS = {
    'search':      '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    'bell':        '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    'menu':        '<path d="M3 12h18M3 6h18M3 18h18"/>',
    'chevron-down':  '<path d="m6 9 6 6 6-6"/>',
    'chevron-left':  '<path d="m15 18-6-6 6-6"/>',
    'chevron-right': '<path d="m9 18 6-6-6-6"/>',
    'close':       '<path d="M18 6 6 18M6 6l12 12"/>',
    'plus':        '<path d="M12 5v14M5 12h14"/>',
    'send':        '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
    'check-circle': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m22 4-10 10.01L9 11"/>',
    'check-simple': '<path d="M20 6 9 17l-5-5"/>',
    'copy':        '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    'paperclip':   '<path d="M21.4 11.05 12.3 20.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8"/>',
    'whatsapp':    '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/>',
    'briefcase-2': '<path d="M20 7h-3V5a2 2 0 0 0-2-2H9a2 2 0 0 0-2 2v2H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1zM9 5h6v2H9z"/>',
    'sparkle':     '<path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"/>',
    'file-pdf':    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 15v-3h1.5a1.5 1.5 0 0 1 0 3H9z"/>',
    'file-x':      '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="m8.5 13 3 5m0-5-3 5"/>',
    'file-blank':  '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    'trash':       '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>',
    'edit':        '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    'eye':         '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    'eye-off':     '<path d="m3 3 18 18M10.6 10.6a3 3 0 0 0 4.2 4.2M9.4 5.3A9.9 9.9 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-2.4 3.3M6.3 6.7A17 17 0 0 0 2 12s3.6 7 10 7c1 0 1.8-.1 2.7-.4"/>',
    'grip':        '<circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/>',
    'arrow-up':    '<path d="M12 19V5M5 12l7-7 7 7"/>',
    'arrow-down':  '<path d="M12 5v14M19 12l-7 7-7-7"/>',
    'upload':      '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 9l5-5 5 5M12 4v12"/>',
    'external':    '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6M10 14 21 3"/>',
    'filter':      '<path d="M22 3H2l8 9.46V19l4 2v-8.54z"/>',
    'clock-2':     '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
}

#: Icons drawn solid rather than stroked.
FILLED_ICONS = {'sparkle', 'filter', 'whatsapp'}
