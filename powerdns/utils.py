"""Utilities for powerdns models"""

import ipaddress

from django import VERSION
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    validate_ipv4_address,
    validate_ipv6_address,
    RegexValidator
)
from django.db import models
from django.utils.translation import ugettext_lazy as _
from dj.choices import Choices


DOMAIN_NAME_RECORDS = ('CNAME', 'MX', 'NAPTR', 'NS', 'PTR')


# Validator for the domain names only in RFC-1035
# PowerDNS considers the whole zone to be invalid if any of the records end
# with a period so this custom validator is used to catch them


# Valid: example.com
# Valid: *.example.com
# Invalid: example.com.
# Invalid: ex*mple.com
validate_domain_name = RegexValidator(
    r'^(\*\.)?([_A-Za-z0-9-]+\.)*([A-Za-z0-9])+$'
)


validate_dn_optional_dot = RegexValidator(
    '^[A-Za-z0-9.-]*$'
)


validate_time = RegexValidator('^[0-9]+$')


def validate_name_equal_to_content(name, content):
    """Validator checks if record name is not equal to content

    In theory NS record can contain same name and content, and with
    glue record configuration it will work. However it is not good
    practice so we are not allowing such configuration.
    """
    if name == content:
        raise ValidationError(
            'Cannot create record with the same name and content'
        )


def validate_soa(value):
    """Validator for a correct SOA record"""
    try:
        name, email, sn, refresh, retry, expiry, nx = value.split()
    except ValueError:
        raise ValidationError(_('Enter a valid SOA record'))
    for subvalue, field in [
        (name, 'Domain name'),
        (email, 'e-mail'),
    ]:
        try:
            validate_dn_optional_dot(subvalue)
        except ValidationError:
            raise ValidationError(
                _('Incorrect {}. Should be a valid domain name.'.format(
                    field
                ))
            )
    for subvalue, field in [
        (sn, 'Serial'),
        (refresh, 'Refresh rate'),
        (retry, 'Retry rate'),
        (expiry, 'Expiry time'),
        (nx, 'Negative resp. time'),
    ]:
        try:
            validate_time(subvalue)
        except ValidationError:
            raise ValidationError(
                _('Incorrect {}. Should be a valid domain name.'.format(
                    field
                ))
            )


class TimeTrackable(models.Model):
    created = models.DateTimeField(
        verbose_name=_("date created"), auto_now=False, auto_now_add=True,
        editable=False,
    )
    modified = models.DateTimeField(
        verbose_name=_('last modified'), auto_now=True, editable=False,
    )

    class Meta:
        abstract = True


class Owned(models.Model):
    """
    DEPRECATED in favour of `powerdns.models.ownership` module.

    Model that has an owner. This owner is set as default to the creator
    of this model, but can be overridden.
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL,
                              null=True, blank=True, on_delete=models.CASCADE)

    class Meta:
        abstract = True


# TODO(mkurek): rename to sth better
def to_reverse(ip):
    """
    Given an ip address it will return a tuple of (domain, number)
    suitable for PTR record

    Example:
    >>> to_reverse('192.168.1.2')
    ('2', '1.1.168.192.in-addr.arpa')
    >>> to_reverse('2001:0db8:0:0::1428:57ab')
    ('b', 'a.7.5.8.2.4.1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa')  # noqa
    """
    return _reverse_ip(ip)


def reverse_pointer(ip):
    """
    Return reversed IP address in PTR format. Handles IPv4 and IPv6.

    Example:
    >>> reverse_pointer('192.168.1.2')
    '2.1.1.168.192.in-addr.arpa'
    >>> reverse_pointer('2001:0db8:0:0::1428:57ab')
    'b.a.7.5.8.2.4.1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa'
    """
    return '.'.join(_reverse_ip(ip))


def _reverse_ip(ip):
    """
    Reverse `ip` to ptr.

    Returns: tuple of (last_byte, domain) suitable for PTR record
    last_byte is the last byte of IPv4 address and last character of IPv6
    address

    Example:
    >>> _reverse_ip('192.168.1.2')
    ('2', '1.1.168.192.in-addr.arpa')
    >>> _reverse_ip('2001:0db8:0:0::1428:57ab')
    ('b', 'a.7.5.8.2.4.1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa')  # noqa
    """
    ip_obj = ipaddress.ip_address(ip)
    if isinstance(ip_obj, ipaddress.IPv6Address):
        last_byte, *first_bytes_reversed = ip_obj.exploded[::-1].replace(':', '')  # noqa
        domain_suffix = 'ip6.arpa'
    else:
        last_byte, *first_bytes_reversed = str(ip_obj).split('.')[::-1]
        domain_suffix = 'in-addr.arpa'
    return last_byte, '.'.join(first_bytes_reversed + [domain_suffix])


class AutoPtrOptions(Choices):
    _ = Choices.Choice
    NEVER = _("Never")
    ALWAYS = _("Always")
    ONLY_IF_DOMAIN = _("Only if domain exists")


DOMAIN_TYPE = (
    ('MASTER', 'MASTER'),
    ('NATIVE', 'NATIVE'),
    ('SLAVE', 'SLAVE'),
)


def format_recursive(template, arguments):
    """
    Performs str.format on the template in a recursive fashion iterating over
    lists and dictionary values

    >>> template = {
    ... 'a': 'Value {a}',
    ... 'b': {
    ...     'a': 'Value {a}',
    ...     'b': 'Value {b}',
    ... },
    ... 'c': ['Value {a}', 'Value {b}'],
    ... 'd': 10,
    ... }
    >>> arguments = {
    ... 'a': 'A',
    ... 'b': 'B',
    ... }
    >>> result = format_recursive(template, arguments)
    >>> result['a']
    'Value A'
    >>> result['b']['b']
    'Value B'
    >>> result['c'][0]
    'Value A'
    >>> result['d']
    10
    """
    if isinstance(template, str):
        return template.format(**arguments)
    elif isinstance(template, dict):
        return {
            k: format_recursive(v, arguments)
            for (k, v) in template.items()
        }
    elif isinstance(template, list):
        return [format_recursive(v, arguments) for v in template]
    else:
        return template


class RecordLike(models.Model):
    """Object validated like a record"""

    class Meta:
        abstract = True

    def get_field(self, name):
        """Get the value of a prefixed or not field"""
        return getattr(self, self.prefix + name)

    def set_field(self, name, value):
        """Set the value of a prefixed or not field"""
        return setattr(self, self.prefix + name, value)

    def clean(self):
        self.clean_content_field()
        self.force_case()
        self.validate_for_conflicts()
        return super(RecordLike, self).clean()

    def clean_content_field(self):
        """Perform a type-dependent validation of content field"""
        type_ = self.get_field('type')
        content = self.get_field('content')
        if type_ == 'A':
            validate_ipv4_address(content)
        elif type_ == 'AAAA':
            validate_ipv6_address(content)
        elif type_ == 'SOA':
            validate_soa(content)
        elif type_ in DOMAIN_NAME_RECORDS:
            validate_domain_name(content)
            validate_name_equal_to_content(self.get_field('name'), content)

    def validate_for_conflicts(self):
        """Ensure this record doesn't conflict with other records."""
        from .models import Record

        def check_unique(comment, **kwargs):
            conflicting = Record.objects.filter(**kwargs)
            record_pk = self.get_record_pk()
            if record_pk is not None:
                conflicting = conflicting.exclude(pk=record_pk)
            if conflicting:
                raise ValidationError(comment.format(
                    ', '.join(str(record.id) for record in conflicting)
                ))
        if self.get_field('type') == 'CNAME':
            check_unique(
                'Cannot create CNAME record. Following conflicting '
                'records exist: {}',
                name=self.get_field('name'),
            )
        else:
            check_unique(
                'Cannot create a record. Following conflicting CNAME'
                'record exists: {}',
                type='CNAME',
                name=self.get_field('name'),
            )

    def force_case(self):
        """Force the name and content case to upper and lower respectively"""
        if self.get_field('name'):
            self.set_field('name', self.get_field('name').lower())
        if self.get_field('type'):
            self.set_field('type', self.get_field('type').upper())


def flat_dict_diff(old_dict, new_dict):
    """
    return: {
        'name': {'old': 'old-value', 'new': 'new-value'},
        'ttl': {'old': 'old-value', 'new': ''},
        'prio': {'old': '', 'new': 'new-value'},
        ..
    }
    """
    def _fmt(old, new):
        return {
            'old': old,
            'new': new,
        }

    diff_result = {}
    keys = set(old_dict) & set(new_dict)
    for key in keys:
        diff_result[key] = _fmt(old_dict[key], new_dict[key])
    return diff_result


def patterns(prefix, *args):
    if VERSION < (1, 9):
        from django.conf.urls import patterns as django_patterns
        return django_patterns(prefix, *args)
    elif prefix != '':
        raise Exception("You need to update your URLConf to be a list of URL "
                        "objects")
    else:
        return list(args)


def find_domain_for_record(record_name):
    """
    Returns matching to provided name Domain object instances.

    Example when 20.10.in-addr.arpa domain exist:
    >>> get_matching_domains(30.20.10.in-addr.arpa)
        <Domain: 20.10.in-addr.arpa>

    Example when only existing-domain.com domain exist:
    >>> get_matching_domains(sub-domain.on.existing-domain.com)
        <Domain: existing-domain.com>
    """
    from .models import Domain
    chunks = record_name.split('.')

    search_partial_domains = [
        '.'.join(chunks[i:]) for i in range(len(chunks))
    ]

    matching_domains = Domain.objects.filter(name__in=search_partial_domains)
    matching_domains = sorted(
        matching_domains, key=lambda domain: len(domain.name), reverse=True
    )
    if matching_domains:
        return matching_domains[0]
    else:
        return None
