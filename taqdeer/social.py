"""Likes and comments, shared by both kinds of recognition.

A praise and a certification are different records but the same social object:
something good happened to somebody, and colleagues want to say so. Rather than
two like tables and two comment tables that drift apart, both point at a
generic target.

Deliberately not Django's contenttypes framework. Two kinds is not enough to
justify a join through a content-type table on every feed render, and a plain
pair of nullable foreign keys keeps the constraint visible: exactly one is set.
"""
from django.contrib.auth.models import User
from django.db import models


class Reaction(models.Model):
    """One person reacting to one thing, with one of four marks.

    Four and no more. An open emoji picker on a recognition wall invites the
    one joke reaction that makes somebody feel worse about being praised, and
    moderating that is not a job anybody wants. These four can only mean
    congratulations.
    """
    HEART = '❤️'
    CLAP = '👏'
    PARTY = '🎉'
    STAR = '🌟'
    CHOICES = [
        (HEART, 'Heart'),
        (CLAP, 'Applause'),
        (PARTY, 'Celebrate'),
        (STAR, 'Brilliant'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='taqdeer_reactions')
    #: One reaction per person per post, so choosing another replaces it. The
    #: default keeps every row written before this field existed meaningful.
    emoji = models.CharField(max_length=8, choices=CHOICES, default=HEART)
    praise = models.ForeignKey('taqdeer.Praise', on_delete=models.CASCADE,
                               null=True, blank=True, related_name='likes')
    certification = models.ForeignKey('taqdeer.Certification',
                                      on_delete=models.CASCADE, null=True,
                                      blank=True, related_name='likes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'praise'],
                                    name='one_like_per_praise'),
            models.UniqueConstraint(fields=['user', 'certification'],
                                    name='one_like_per_certification'),
        ]

    def __str__(self):
        return f'{self.user} likes {self.praise or self.certification}'


class Comment(models.Model):
    """A note under a praise or a certification."""
    author = models.ForeignKey(User, on_delete=models.CASCADE,
                               related_name='taqdeer_comments')
    praise = models.ForeignKey('taqdeer.Praise', on_delete=models.CASCADE,
                               null=True, blank=True, related_name='comments')
    certification = models.ForeignKey('taqdeer.Certification',
                                      on_delete=models.CASCADE, null=True,
                                      blank=True, related_name='comments')
    text = models.TextField()
    #: Recorded rather than re-parsed. Who was named is a fact about the moment
    #: the comment was written, and a username that changes later must not
    #: quietly rewrite who was told.
    mentions = models.ManyToManyField(
        User, blank=True, related_name='taqdeer_comment_mentions',
        help_text='Users @mentioned in this comment.')
    created_at = models.DateTimeField(auto_now_add=True)
    #: Set when the author changes it, so the thread can say so. A comment that
    #: silently changes after somebody replied to it is worse than one that
    #: cannot change at all.
    edited_at = models.DateTimeField(null=True, blank=True)

    #: HR can take one down without deleting it, the same as a praise.
    hidden_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.author}: {self.text[:40]}'
