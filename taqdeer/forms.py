"""Forms for the HR side of AJi Taqdeer.

Model forms rather than hand-written fields: the model already carries the
labels, the help text and the choices from the specification, so writing them
a second time here is two places to keep in step and one of them will drift.
What this file does add is grouping — the settings page has thirty-odd fields
and a flat list of thirty inputs is not a settings page.
"""
from django import forms
from django.contrib.auth.models import Group, User

from .models import AwardCycle, Certification, TaqdeerSettings


class _Styled(forms.ModelForm):
    """Applies the Hub's input classes without repeating them per field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                continue
            if isinstance(w, forms.SelectMultiple):
                w.attrs.setdefault('class', 'tq-in tq-multi')
                w.attrs.setdefault('size', '6')
            elif isinstance(w, (forms.Select, forms.NumberInput, forms.TextInput,
                                forms.Textarea, forms.DateInput, forms.EmailInput)):
                w.attrs.setdefault('class', 'tq-in')
            if isinstance(w, forms.Textarea):
                w.attrs.setdefault('rows', 3)


class PeriodForm(_Styled):
    """Opening or editing one quarter.

    Eligibility is left empty by default and means "whoever the role rules
    already allow" rather than "nobody" — a period opened in a hurry without
    configuring it must still work.
    """
    class Meta:
        model = AwardCycle
        # No group fields. HR names the people who may nominate; everybody
        # else in the directory can receive.
        fields = ['year', 'quarter', 'opens_on', 'closes_on', 'release_on',
                  'awards_per_manager', 'authorised_managers', 'note',
                  'visibility_days']
        widgets = {
            'opens_on': forms.DateInput(attrs={'type': 'date'}),
            'closes_on': forms.DateInput(attrs={'type': 'date'}),
            'release_on': forms.DateInput(attrs={'type': 'date'}),
            'note': forms.Textarea(attrs={'rows': 2}),
        }
        labels = {
            'opens_on': 'Nominations open', 'closes_on': 'Nominations close',
            'release_on': 'Planned release',
            'note': 'A line for the managers you invite',
            # Short enough to stay on one line. "How many people each manager
            # may put forward" wrapped to two, which pushed this input a line
            # below the date field beside it — the row read as misaligned rather
            # than as a pair. The help text under the box still says it in full.
            'awards_per_manager': 'Awards per manager',
            'authorised_managers': 'Who may nominate this quarter',
            'visibility_days': 'Show the results for (days)',
        }
        help_texts = {
            'visibility_days': 'Leave empty to use the value in Settings.',
            'authorised_managers': 'Search and add people. Leave empty to invite '
                                   'everyone who has a direct report.',
            'note': 'Appears in the invitation and on their nomination form.',
        }
        #: Django's default here is "Award cycle with this Year and Quarter
        #: already exists", which names a table rather than telling HR what to
        #: do about it.
        error_messages = {
            forms.models.NON_FIELD_ERRORS: {
                'unique_together': 'That quarter already has a period. Open the '
                                   'existing one from the list above, or pick a '
                                   'different year or quarter.',
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['authorised_managers'].queryset = User.objects.filter(is_active=True)
        self.fields['authorised_managers'].required = False
        self.fields['note'].required = False

    def people_preset(self):
        return {'authorised_managers': (list(self.instance.authorised_managers.all())
                                        if self.instance.pk else [])}

    def clean(self):
        data = super().clean()
        opens, closes = data.get('opens_on'), data.get('closes_on')
        if opens and closes and closes < opens:
            self.add_error('closes_on', 'The window cannot close before it opens.')
        release = data.get('release_on')
        if closes and release and release < closes:
            self.add_error('release_on',
                           'Results cannot be released before submissions close.')
        return data


class CertificationForm(_Styled):
    class Meta:
        model = Certification
        # No logo field. The AJi wordmark is on every card, and asking HR to
        # find a PMI badge before they can congratulate somebody is exactly
        # the friction this feature exists to remove.
        fields = ['employee', 'name', 'issuer', 'obtained_on', 'published']
        widgets = {'obtained_on': forms.DateInput(attrs={'type': 'date'})}
        labels = {'name': 'Certification', 'obtained_on': 'Obtained on',
                  'issuer': 'Issuing body'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employee'].queryset = (
            User.objects.filter(is_active=True).order_by('first_name', 'username'))


class SettingsForm(_Styled):
    """Everything the specification calls configurable, in one form.

    FIELDSETS is the page's structure, not decoration: thirty inputs in a flat
    column is a wall, and the four groups the spec names are the four groups
    somebody actually thinks in.
    """
    #: The page's structure, and it is deliberately not the model's field order.
    #: These are grouped by the question HR is answering, and each group leads
    #: with the one setting they are most likely to want.
    FIELDSETS = [
        ('Who can do what',
         'Two questions, and the usual answer to both is "leave it empty".', [
             'eligible_submitters', 'praise_givers', 'praise_needs_approval',
             'praise_allow_attachment']),
        ('Running a quarter',
         'Defaults for a new Athar period. Any of them can be changed on the '
         'period itself.', [
             'default_period_days', 'default_awards_per_manager',
             'results_visibility_days', 'results_visibility_custom']),
        ('What the certificate says',
         'The wording printed on every Athar certificate, and who signs it.', [
             'certificate_title', 'certificate_recognition_line',
             'signatory_title', 'signatory_name', 'signature_image']),
        ('What the Hub sends',
         'Every message the module can send. Untick one and it stops.', [
             'notify_period_draft',
             'notify_period_open', 'notify_manager_assigned',
             'notify_reminder', 'reminder_days_before',
             'notify_submission', 'notify_returned', 'notify_approved',
             'notify_release', 'congratulations_message']),
        ('Certification announcements',
         'The sentence used in the email and where announcements appear. The '
         'card itself is drawn for you.', [
             'certification_template', 'certification_on_homepage',
             'certification_on_taqdeer', 'certification_on_profile']),
    ]

    #: Rendered with the directory picker rather than a list box of usernames.
    PEOPLE_FIELDS = ('eligible_submitters', 'praise_givers')

    class Meta:
        model = TaqdeerSettings
        exclude = ['updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.PEOPLE_FIELDS:
            self.fields[f].required = False
            self.fields[f].queryset = User.objects.filter(is_active=True)

    def clean_certification_template(self):
        """A template that cannot be filled in is worse than the default one."""
        value = self.cleaned_data['certification_template']
        try:
            value.format(employee='X', certification='Y')
        except (KeyError, IndexError) as exc:
            raise forms.ValidationError(
                f'{exc} is not a field this template can use. Use '
                '{employee} and {certification} only.')
        return value

    def fieldsets(self):
        """(title, description, [bound fields]) for the template to walk."""
        for title, blurb, names in self.FIELDSETS:
            yield title, blurb, [self[n] for n in names if n in self.fields]

    def people_fields(self):
        return {n for n in self.PEOPLE_FIELDS if n in self.fields}

    def people_preset(self):
        """{field: [chosen users]} so the picker can draw its chips.

        A bound field cannot give this: its value is a list of primary keys and
        the picker needs names to show.
        """
        out = {}
        for name in self.people_fields():
            if self.instance.pk:
                out[name] = list(getattr(self.instance, name).all())
            else:
                out[name] = []
        return out
