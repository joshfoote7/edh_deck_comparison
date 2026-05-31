import sys
import re
import configparser
import requests


CONFIG_FILE = 'config.ini'


def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return config


def archidekt_login(username, password):
    session = requests.Session()
    response = session.post(
        'https://archidekt.com/api/rest-auth/login/',
        json={'username': username, 'password': password}
    )
    response.raise_for_status()
    data = response.json()
    token = data.get('key') or data.get('token') or data.get('access') or data.get('access_token')
    if token:
        session.headers.update({'Authorization': f'Token {token}'})
    return session


def add_to_maybeboard(deck_id, card_names, session):
    succeeded = []
    failed = []
    for name in card_names:
        payload = {
            'card': {'oracleCard': {'name': name}},
            'quantity': 1,
            'categories': [{'name': 'Maybeboard', 'includedInDeck': False}],
        }
        response = session.post(
            f'https://archidekt.com/api/decks/{deck_id}/cards/',
            json=payload,
        )
        if response.ok:
            succeeded.append(name)
        else:
            failed.append((name, response.status_code, response.text))
    return succeeded, failed


def fetch_archidekt(url):
    match = re.search(r'/decks/(\d+)', url)
    if not match:
        raise ValueError(f"Couldn't parse deck ID from URL: {url}")
    deck_id = match.group(1)
    api_url = f"https://archidekt.com/api/decks/{deck_id}/"
    response = requests.get(api_url)
    response.raise_for_status()
    data = response.json()
    cards = set()
    for card in data.get('cards', []):
        name = card.get('card', {}).get('oracleCard', {}).get('name', '')
        if name:
            cards.add(name.lower())
    return deck_id, cards


def fetch_edhrec(url):
    match = re.search(r'edhrec\.com/(.+)', url)
    if not match:
        raise ValueError(f"Couldn't parse commander path from URL: {url}")
    path = match.group(1).rstrip('/')
    api_url = f"https://json.edhrec.com/pages/{path}.json"
    response = requests.get(api_url)
    response.raise_for_status()
    data = response.json()
    cards = {}
    for card_list in data.get('container', {}).get('json_dict', {}).get('cardlists', []):
        section = card_list.get('tag', '') or card_list.get('header', 'Unknown')
        for card in card_list.get('cardviews', []):
            name = card.get('name', '')
            if not name:
                continue
            key = name.lower()
            if key in cards:
                cards[key]['sections'].append(section)
            else:
                cards[key] = {
                    'name': name,
                    'inclusion': card.get('inclusion', 0),
                    'synergy': card.get('synergy', 0),
                    'sections': [section],
                }
    return cards


def fetch_scryfall(card_names):
    results = {}
    names = list(card_names)
    for i in range(0, len(names), 75):
        batch = names[i:i + 75]
        identifiers = [{'name': n} for n in batch]
        response = requests.post(
            'https://api.scryfall.com/cards/collection',
            json={'identifiers': identifiers}
        )
        response.raise_for_status()
        data = response.json()
        for card in data.get('data', []):
            name = card.get('name', '')
            if not name:
                continue
            results[name.lower()] = {
                'mana_cost': card.get('mana_cost', ''),
                'type_line': card.get('type_line', ''),
                'oracle_text': card.get('oracle_text', ''),
                'power': card.get('power'),
                'toughness': card.get('toughness'),
                'loyalty': card.get('loyalty'),
            }
    return results


def format_stats(scryfall_data):
    parts = []
    if scryfall_data.get('power') is not None:
        parts.append(f"{scryfall_data['power']}/{scryfall_data['toughness']}")
    if scryfall_data.get('loyalty') is not None:
        parts.append(f"Loyalty: {scryfall_data['loyalty']}")
    return '  '.join(parts) if parts else ''


def wrap_text(text, width, indent):
    words = text.split()
    lines = []
    current = ''
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = (current + ' ' + word).strip()
    if current:
        lines.append(current)
    return ('\n' + ' ' * indent).join(lines)


def print_cards(cards_to_print, scryfall, show_rank=True):
    print("=" * 80)
    for i, card in enumerate(cards_to_print, 1):
        name = card['name']
        sections = ', '.join(card['sections'])
        sf = scryfall.get(name.lower(), {})
        stats = format_stats(sf)

        rank = f"#{i}  " if show_rank else ""
        print(f"  {rank}{name}  {sf.get('mana_cost', '')}")
        print(f"  {sf.get('type_line', '')}" + (f"  —  {stats}" if stats else ''))
        oracle = sf.get('oracle_text', '')
        if oracle:
            print(f"  {wrap_text(oracle, width=70, indent=2)}")
        print(f"  Inclusion: {card['inclusion']}   Synergy: {card['synergy']:.0%}   Sections: {sections}")
        print("-" * 80)


def prompt_maybeboard(sorted_missing, deck_id, config):
    answer = input("\nWould you like to add cards to your Maybeboard? (y/n): ").strip().lower()
    if answer != 'y':
        return

    print("\nEnter the numbers of the cards you want to add, separated by commas.")
    print("Example: 1, 3, 5")
    raw = input("Cards to add: ").strip()

    try:
        picks = [int(x.strip()) for x in raw.split(',') if x.strip()]
    except ValueError:
        print("Invalid input — please enter numbers only.")
        return

    selected = []
    for pick in picks:
        if 1 <= pick <= len(sorted_missing):
            selected.append(sorted_missing[pick - 1])
        else:
            print(f"  Skipping #{pick} — out of range.")

    if not selected:
        print("No valid cards selected.")
        return

    print("\nCards selected to add to Maybeboard:")
    for card in selected:
        print(f"  {card['name']}")

    confirm = input("\nConfirm? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        return

    username = config.get('archidekt', 'username', fallback='').strip()
    password = config.get('archidekt', 'password', fallback='').strip()

    if not username or not password:
        print(f"\nNo credentials found in {CONFIG_FILE}. Please fill in your username and password.")
        return

    print("\nLogging in to Archidekt...")
    try:
        session = archidekt_login(username, password)
    except Exception as e:
        print(f"  Login failed: {e}")
        return
    print("  Logged in.")

    print("Adding cards to Maybeboard...")
    succeeded, failed = add_to_maybeboard(deck_id, [c['name'] for c in selected], session)

    if succeeded:
        print(f"\nSuccessfully added ({len(succeeded)}):")
        for name in succeeded:
            print(f"  {name}")
    if failed:
        print(f"\nFailed to add ({len(failed)}):")
        for name, status, body in failed:
            print(f"  {name}  →  HTTP {status}: {body}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python compare.py <archidekt_url> <edhrec_url>")
        print("")
        print("Example:")
        print("  python compare.py https://archidekt.com/decks/12345 https://edhrec.com/commanders/atraxa-praetors-voice")
        sys.exit(1)

    archidekt_url, edhrec_url = sys.argv[1], sys.argv[2]
    config = load_config()

    print("Fetching deck from Archidekt...")
    try:
        deck_id, deck = fetch_archidekt(archidekt_url)
    except Exception as e:
        print(f"  Error fetching Archidekt deck: {e}")
        sys.exit(1)
    print(f"  Found {len(deck)} cards in deck.")

    print("Fetching synergy cards from EDHREC...")
    try:
        edhrec_cards = fetch_edhrec(edhrec_url)
    except Exception as e:
        print(f"  Error fetching EDHREC page: {e}")
        sys.exit(1)
    print(f"  Found {len(edhrec_cards)} cards on EDHREC page.")

    missing = {k: v for k, v in edhrec_cards.items() if k not in deck}
    sorted_missing = sorted(missing.values(), key=lambda c: c['synergy'], reverse=True)

    print(f"  Fetching card details from Scryfall for {len(missing)} cards...")
    try:
        scryfall = fetch_scryfall([c['name'] for c in sorted_missing])
    except Exception as e:
        print(f"  Warning: Scryfall lookup failed ({e}). Showing results without card details.")
        scryfall = {}

    print(f"\nTop 10 cards on EDHREC not in your deck (out of {len(missing)}):\n")
    print_cards(sorted_missing[:10], scryfall)

    displayed = sorted_missing[:10]

    if len(sorted_missing) > 10:
        answer = input(f"\nShow all {len(missing)} cards? (y/n): ").strip().lower()
        if answer == 'y':
            print(f"\nAll {len(missing)} cards:\n")
            print_cards(sorted_missing, scryfall)
            displayed = sorted_missing

    prompt_maybeboard(displayed, deck_id, config)


if __name__ == '__main__':
    main()
