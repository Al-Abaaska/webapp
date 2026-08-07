"""Trusted Somali publishers and their cross-platform official identities."""
import re
from urllib.parse import unquote, urlsplit


TRUSTED_PUBLISHERS = [
    {
        'name': 'VOA Somali',
        'domains': ('voasomali.com',),
        'url': 'https://www.voasomali.com',
        'type': 'Independent media',
        'credibility': 'Strong independent secondary source',
        'youtube_ids': ('UCDKfJDvK3EEdbxGFA4roVQA',),
        'youtube_handles': ('@voasomali', '/voasomalia'),
        'facebook_fragments': ('/voasomali/', '/pages/voasomali/108173635871713'),
        'x_handles': ('voasomali',),
        'aliases': ('voa somali', 'voa somalia'),
    },
    {
        'name': 'BBC News Somali',
        'domains': ('bbc.com', 'bbc.co.uk'),
        'website_path_prefixes': ('/somali',),
        'search_site': 'bbc.com/somali',
        'url': 'https://www.bbc.com/somali',
        'type': 'Independent media',
        'credibility': 'Strong independent secondary source',
        'youtube_ids': ('UC_QuRsbRQpaC4iS8WlxW0NA',),
        'youtube_handles': ('@bbcnewssomali',),
        'facebook_fragments': ('/bbcnewssomali',),
        'x_handles': ('bbcsomali',),
        'aliases': ('bbc news somali', 'bbc somali'),
    },
    {
        'name': 'SONNA',
        'domains': ('sonna.so',),
        'url': 'https://sonna.so',
        'type': 'Official state news agency',
        'credibility': 'Official primary government source',
        'youtube_ids': ('UCglVaYHg00lmYe7cqEnu9MQ',),
        'youtube_handles': ('@sonna',),
        'facebook_fragments': ('/sonna', '/sonnaagency'),
        'x_handles': ('sonnalive', 'sonnaagency'),
        'aliases': ('sonna', 'somali national news agency'),
    },
    {
        'name': 'SNTV – Somali National Television',
        'domains': ('sntv.so', 'sntvnews.com'),
        'url': 'https://sntv.so',
        'type': 'State broadcaster',
        'credibility': 'Official primary government source',
        'youtube_ids': ('UCi5fZhV7tPitSjnhEHJirGA',),
        'youtube_handles': ('@sntvnews',),
        'facebook_fragments': ('/sntvnews', '/somalinationaltelevision'),
        'x_handles': ('sntvnews1',),
        'aliases': ('sntv', 'sntv news', 'somali national television'),
    },
    {
        'name': 'Villa Somalia',
        'domains': ('villasomalia.gov.so',),
        'url': 'https://villasomalia.gov.so',
        'type': 'Official institution',
        'credibility': 'Primary source for presidential statements',
        'youtube_ids': ('UC5G86jQPrQNvNjcp6PEl0nQ',),
        'youtube_handles': ('@xafiiskawarfaafinta',),
        'facebook_fragments': ('/villasomaliaofficial',),
        'x_handles': ('thevillasomalia',),
        'aliases': ('villa somalia', 'xafiiska warfaafinta'),
    },
    {
        'name': 'Ministry of Information Somalia',
        'domains': ('moi.gov.so',),
        'url': 'https://moi.gov.so',
        'type': 'Official institution',
        'credibility': 'Primary source for government announcements',
        'youtube_ids': (),
        'youtube_handles': (),
        'facebook_fragments': ('/moictsomalia', '/moi.somalia'),
        'x_handles': ('moictsomalia',),
        'aliases': ('ministry of information somalia', 'moict somalia'),
    },
    {
        'name': 'Federal Parliament of Somalia',
        'domains': ('parliament.gov.so',),
        'url': 'https://parliament.gov.so',
        'type': 'Official institution',
        'credibility': 'Primary source for parliamentary decisions',
        'youtube_ids': ('UCnrItmttsphfon9517a0w7Q',),
        'youtube_handles': ('@golahashacabka',),
        'facebook_fragments': ('/golahashacabkajfs',),
        'x_handles': (),
        'aliases': ('parliament of somalia', 'golaha shacabka', 'federal parliament of somalia'),
    },
    {
        'name': 'Office of the Prime Minister Somalia',
        'domains': ('opm.gov.so',),
        'url': 'https://opm.gov.so',
        'type': 'Official institution',
        'credibility': 'Primary source for PM and government statements',
        'youtube_ids': ('UCApasvFUoAg4mxR4ZBjicQg',),
        'youtube_handles': ('/user/opmsomalia', '@opmsomalia'),
        'facebook_fragments': ('/opmsomalia',),
        'x_handles': ('opmsomalia',),
        'aliases': ('office of the prime minister somalia', 'opm somalia'),
    },
    {
        'name': 'Hiiraan Online',
        'domains': ('hiiraan.com',),
        'url': 'https://www.hiiraan.com',
        'type': 'Independent media',
        'credibility': 'Independent supporting source',
        'youtube_ids': ('UCVxnq-1bcF-aQHOhYzigPrw',),
        'youtube_handles': ('@holtv5095',),
        'facebook_fragments': ('/hiiraanpage',),
        'x_handles': ('hiiraan',),
        'aliases': ('hiiraan online', 'hiiraanonline', 'hol tv'),
    },
    {
        'name': 'Garowe Online',
        'domains': ('garoweonline.com',),
        'url': 'https://www.garoweonline.com',
        'type': 'Independent media',
        'credibility': 'Independent supporting source',
        'youtube_ids': ('UCglgANrl74ypR0ihi8K9uMw',),
        'youtube_handles': ('/user/garoweonline', '@garoweonline'),
        'facebook_fragments': ('/garoweonline',),
        'x_handles': ('garoweonline',),
        'aliases': ('garowe online',),
    },
    {
        'name': 'Caasimada Online',
        'domains': ('caasimada.net',),
        'url': 'https://www.caasimada.net',
        'type': 'Independent media',
        'credibility': 'Independent supporting source',
        'youtube_ids': ('UC5Z0H3KP_0WEywDfc126ezQ',),
        'youtube_handles': ('/user/caasimadda', '@caasimadda'),
        'facebook_fragments': ('/caasimadda',),
        'x_handles': ('caasimadda',),
        'aliases': ('caasimada online', 'caasimadda online', 'caasimada'),
    },
    {
        'name': 'Shabelle Media Network',
        'domains': ('shabellemedia.com', 'shabelletv.com', 'shabelle.net', 'radioshabelle.com'),
        'url': 'https://shabellemedia.com',
        'type': 'Independent media',
        'credibility': 'Independent supporting source',
        'youtube_ids': ('UCQi_zlvU6TtqMMR56OQYkZA', 'UCXSorfoPFZbMraJP4XCtDQw'),
        'youtube_handles': ('@shabellechannel9816', '@shabelletv5037'),
        'facebook_fragments': ('/shabellemedianetwork', '/shabellemedia', '/radioshabelle'),
        'x_handles': ('shabellemedia',),
        'aliases': ('shabelle media network', 'shabelle media', 'shabelle tv', 'radio shabelle'),
    },
]


def _normalise_name(value):
    return re.sub(r'[^\w]+', ' ', str(value or '').casefold()).strip()


def identify_trusted_publisher(item):
    """Return a publisher profile only when its website or social identity matches."""
    url = unquote(str(item.get('url') or '')).strip()
    author_url = unquote(str(item.get('author_url') or '')).strip()
    combined_url = f'{url} {author_url}'.casefold()
    channel_id = str(item.get('channel_id') or '').strip()
    source_names = {
        _normalise_name(item.get('source')),
        _normalise_name(item.get('author')),
    }

    try:
        parsed = urlsplit(url)
        host = parsed.netloc.casefold().split(':')[0]
        path = unquote(parsed.path or '').casefold()
    except Exception:
        host, path = '', ''

    for profile in TRUSTED_PUBLISHERS:
        domains = profile.get('domains', ())
        if any(host == domain or host.endswith('.' + domain) for domain in domains):
            prefixes = profile.get('website_path_prefixes', ())
            if not prefixes or any(path.startswith(prefix) for prefix in prefixes):
                return {**profile, 'identity_method': 'official_website'}

        if channel_id and channel_id in profile.get('youtube_ids', ()):
            return {**profile, 'identity_method': 'youtube_channel_id'}
        if 'youtube.com' in combined_url or 'youtu.be' in combined_url:
            if any(handle.casefold() in combined_url for handle in profile.get('youtube_handles', ())):
                return {**profile, 'identity_method': 'youtube_handle'}
        if 'facebook.com' in combined_url:
            if any(fragment.casefold() in combined_url for fragment in profile.get('facebook_fragments', ())):
                return {**profile, 'identity_method': 'facebook_page'}
        if ('twitter.com' in combined_url or 'x.com' in combined_url):
            if any(f'/{handle.casefold()}' in combined_url for handle in profile.get('x_handles', ())):
                return {**profile, 'identity_method': 'x_account'}

        # Name matching is only a fallback when platform metadata itself
        # supplies the publisher name. It never overrides a conflicting ID.
        aliases = {_normalise_name(alias) for alias in profile.get('aliases', ())}
        if channel_id == '' and item.get('content_origin') in ('platform', 'platform_metadata'):
            if any(name and name in aliases for name in source_names):
                return {**profile, 'identity_method': 'platform_publisher_name'}
    return None


SOMALI_SOURCES = [
    {
        'name': profile['name'],
        'domain': profile['domains'][0],
        'search_site': profile.get('search_site', profile['domains'][0]),
        'url': profile['url'],
        'type': profile['type'],
        'credibility': profile['credibility'],
    }
    for profile in TRUSTED_PUBLISHERS
]
SOMALI_DOMAINS = [source['domain'] for source in SOMALI_SOURCES]
SOMALI_SOURCE_MAP = {
    domain: profile['name']
    for profile in TRUSTED_PUBLISHERS
    for domain in profile['domains']
}
