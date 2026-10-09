# core/certs.py
"""Certificates for serving the Hub over https on the office network.

This exists because of the microphone. A browser will not hand a page a
microphone or a screen unless it considers the page secure, and "secure" means
https — with the single exception of localhost. So voice calls work when you
develop on your own machine and stop working the moment a colleague opens the
Hub at http://172.12.0.190:8000, where `navigator.mediaDevices` is not merely
refused but absent. The call button would appear to do nothing.

**A small authority, not a lone certificate.** The obvious thing is one
self-signed certificate. The trouble with it is that trusting it is per
certificate: every machine has to be told to trust that exact file, and told
again the day the server's address changes and it has to be reissued. Instead
this makes a tiny certificate authority once, keeps it, and issues the server's
certificate from it. IT installs the *authority* on the estate once — one
Group Policy setting — and every certificate this ever issues is trusted, today
and after the next address change.

**Reissued when it no longer fits.** The certificate names the machine: its
hostname, localhost, and every address it answers on. Machines move between
networks and pick up new addresses, and a certificate that does not name the
address in the browser's URL bar is a certificate the browser rejects. So the
names are checked on every start and the certificate is reissued when it has
stopped covering them, rather than left to be discovered as an error.

This is for the office network. It is not a substitute for a certificate from
AJi's own CA or a public one on anything reachable from outside; what it gives
is a secure context on the LAN, which is what the microphone is waiting for.
"""
import datetime
import ipaddress
import socket

from django.conf import settings

#: Both files live here, and one of them is a private key — anybody who takes a
#: copy can impersonate this server to anybody who trusts the authority. It is
#: not inside `static/` or `media/`, so nothing serves it, and it should not go
#: into source control.
def cert_dir():
    path = getattr(settings, 'SSL_CERT_DIR', None) or (settings.BASE_DIR / 'certs')
    path.mkdir(parents=True, exist_ok=True)
    return path


def paths():
    d = cert_dir()
    return {
        'ca_cert': d / 'aji-hub-ca.crt',
        'ca_key': d / 'aji-hub-ca.key',
        'cert': d / 'aji-hub.crt',
        'key': d / 'aji-hub.key',
    }


#: Ten years for the authority, because installing it on the estate is the part
#: that costs somebody an afternoon and nobody wants to repeat it. Two years for
#: the server's own certificate, which is reissued here in a second.
CA_YEARS = 10
CERT_DAYS = 825


def configured_hosts():
    """Names from settings, for the ones the machine cannot work out itself.

    `local_names` asks the machine what it is called, which is right for a
    laptop and not enough for a server. Staff reach the Hub by whatever name IT
    put in DNS — `ajihub`, `hub.ajigroup.com` — and an alias is not something
    the server knows it has: `gethostname` still answers with the computer name.
    A certificate that does not name the alias is rejected for it, however well
    the authority is trusted, and the browser says the certificate is invalid
    rather than that it is missing a name.

    So the alias is the one thing that does have to be written down. It belongs
    in settings rather than on the command line because the server is started
    by a service or a scheduled task, and a flag left out of that is a flag that
    is gone.
    """
    return getattr(settings, 'SSL_CERT_HOSTS', None) or ()


def local_names():
    """Every name and address this machine answers to.

    Gathered rather than configured: the address the Hub is reached on is a fact
    about the network, and asking somebody to keep a list of them in a settings
    file is asking them to forget.
    """
    names = {'localhost'}
    ips = {'127.0.0.1', '::1'}
    try:
        host = socket.gethostname()
        names.add(host)
        names.add(host.lower())
        for info in socket.getaddrinfo(host, None):
            addr = info[4][0]
            # A link-local IPv6 address carries a %interface suffix that is
            # meaningless to anybody else and is not a certificate name.
            if '%' not in addr:
                ips.add(addr)
    except OSError:
        pass
    return names, ips


def _split(hosts):
    """Sort a mixed pile of names and addresses into the two the SAN needs."""
    names, ips = set(), set()
    for h in hosts or ():
        h = (h or '').strip()
        if not h or h in ('0', '0.0.0.0', '::'):
            # What "listen on everything" is spelled as. It is not a name any
            # browser will ever ask for.
            continue
        try:
            ips.add(str(ipaddress.ip_address(h)))
        except ValueError:
            names.add(h)
    return names, ips


def _covers(cert, names, ips):
    """Does this certificate still name everything we need it to?"""
    from cryptography import x509
    try:
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    have_names = {n.lower() for n in san.get_values_for_type(x509.DNSName)}
    have_ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    return ({n.lower() for n in names} <= have_names) and (ips <= have_ips)


def _load(path):
    from cryptography import x509
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except Exception:                                          # noqa: BLE001
        # A truncated or hand-edited file is not worth a traceback: it is
        # replaced on the next line either way.
        return None


def _expired(cert, within_days=30):
    now = datetime.datetime.now(datetime.timezone.utc)
    return cert.not_valid_after_utc <= now + datetime.timedelta(days=within_days)


def _write_private(path, key):
    from cryptography.hazmat.primitives import serialization
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        # Windows does not model permissions this way. The directory is inside
        # the project, which is the protection there is.
        pass


def ensure_ca():
    """The authority, made once and then left alone.

    Returns (certificate, private key). Regenerating it is deliberately not
    routine — every machine that trusts the old one would have to be told about
    the new one, which is the cost this whole arrangement exists to pay once.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    p = paths()
    if p['ca_cert'].exists() and p['ca_key'].exists():
        cert = _load(p['ca_cert'])
        if cert is not None and not _expired(cert):
            key = serialization.load_pem_private_key(p['ca_key'].read_bytes(),
                                                     password=None)
            return cert, key

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Arabtech Jardaneh'),
        x509.NameAttribute(NameOID.COMMON_NAME, 'AJi Hub Local CA'),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)                       # self-signed: it is the root
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365 * CA_YEARS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0),
                           critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .sign(key, hashes.SHA256()))

    p['ca_cert'].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _write_private(p['ca_key'], key)
    return cert, key


def ensure(extra_hosts=(), force=False):
    """The server's certificate, reissued if it no longer fits.

    Returns (cert_path, key_path, what_happened) where the last is 'kept',
    'issued' or 'reissued' — the caller says which out loud, because a
    certificate quietly changing under somebody is how you end up explaining a
    browser warning that was not there yesterday.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    p = paths()
    names, ips = local_names()
    more_names, more_ips = _split(list(configured_hosts()) + list(extra_hosts or ()))
    names |= more_names
    ips |= more_ips

    existing = _load(p['cert']) if p['cert'].exists() else None
    if (not force and existing is not None and p['key'].exists()
            and not _expired(existing) and _covers(existing, names, ips)):
        return p['cert'], p['key'], 'kept'

    ca_cert, ca_key = ensure_ca()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)

    # Sorted, so that a certificate covering the same machine twice running is
    # byte-identical apart from its serial — it makes "did this change?"
    # answerable by looking.
    san = ([x509.DNSName(n) for n in sorted(names)] +
           [x509.IPAddress(ipaddress.ip_address(i)) for i in sorted(ips)])

    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Arabtech Jardaneh'),
                x509.NameAttribute(NameOID.COMMON_NAME, 'AJi Hub'),
            ]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                           critical=True)
            # The names the browser checks. A certificate is trusted *for* the
            # address in the URL bar, so this list is the whole point of it.
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier
                           .from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))

    # The authority's own certificate is appended to the server's. A browser
    # that already trusts the authority needs only the leaf, but everything else
    # — curl, Python, a phone — wants to be handed the chain rather than made to
    # go and find it.
    p['cert'].write_bytes(cert.public_bytes(serialization.Encoding.PEM) +
                          ca_cert.public_bytes(serialization.Encoding.PEM))
    _write_private(p['key'], key)
    return p['cert'], p['key'], ('reissued' if existing is not None else 'issued')


def described(cert_path):
    """What a certificate says, for printing. Names, and when it runs out."""
    from cryptography import x509
    cert = _load(cert_path)
    if cert is None:
        return {'names': [], 'expires': None}
    try:
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        names = ([str(n) for n in san.get_values_for_type(x509.DNSName)] +
                 [str(i) for i in san.get_values_for_type(x509.IPAddress)])
    except x509.ExtensionNotFound:
        names = []
    return {'names': names, 'expires': cert.not_valid_after_utc.date().isoformat()}
