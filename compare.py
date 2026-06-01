import sys
import os
import re
import csv
import configparser
import random
import string
from datetime import datetime
import requests


CONFIG_FILE = 'config.ini'


def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return config


def archidekt_login(username, password):
    session = requests.Session()
    session.get('https://archidekt.com/login')
    csrf = session.cookies.get('csrftoken', '')
    headers = {'X-CSRFToken': csrf, 'Referer': 'https://archidekt.com/login'} if csrf else {}
    response = session.post(
        'https://archidekt.com/api/rest-auth/login/',
        json={'username': username, 'password': password},
        headers=headers
    )
    response.raise_for_status()
    data = response.json()
    token = data.get('key') or data.get('token') or data.get('access') or data.get('access_token')
    if not token:
        raise ValueError(f"Login succeeded but no token found. Response keys: {list(data.keys())}")
    session.headers.update({
        'Authorization': f'JWT {token}',
        'X-CSRFToken': session.cookies.get('csrftoken', csrf),
        'Referer': 'https://archidekt.com',
    })
    return session


def random_patch_id(length=9):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def lookup_archidekt_card_id(name, session):
    response = session.get(
        'https://archidekt.com/api/cards/v2/',
        params={'nameSearch': name, 'includeTokens': '', 'includeDigital': '', 'unique': ''}
    )
    response.raise_for_status()
    data = response.json()
    results = data.get('results', [])
    for card in results:
        oracle = card.get('oracleCard', {})
        if oracle.get('name', '').lower() == name.lower():
            return str(card.get('id'))
    if results:
        return str(results[0].get('id'))
    return None


def add_to_maybeboard(deck_id, card_names, session):
    succeeded = []
    failed = []
    cards_payload = []

    print("  Looking up card IDs...")
    for name in card_names:
        card_id = lookup_archidekt_card_id(name, session)
        if not card_id:
            print(f"  Could not find Archidekt ID for: {name}")
            failed.append((name, 'N/A', 'Card not found in Archidekt'))
            continue
        cards_payload.append({
            'action': 'add',
            'cardid': card_id,
            'customCardId': None,
            'categories': ['Maybeboard'],
            'patchId': random_patch_id(),
            'modifications': {
                'quantity': 1,
                'modifier': 'Normal',
                'customCmc': None,
                'companion': False,
                'flippedDefault': False,
                'label': ',#656565',
            }
        })

    if not cards_payload:
        return succeeded, failed

    response = session.patch(
        f'https://archidekt.com/api/decks/{deck_id}/modifyCards/v2/',
        json={'cards': cards_payload},
    )
    if response.ok:
        succeeded.extend(card_names)
    else:
        print(f"  Debug — HTTP {response.status_code}: {response.text[:300]}")
        for name in card_names:
            failed.append((name, response.status_code, response.text))
    return succeeded, failed


def fetch_archidekt(url):
    """Fetch deck cards as a simple set of lowercase names."""
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


def fetch_archidekt_full(url):
    """Fetch deck with card categories and deck name for analysis."""
    match = re.search(r'/decks/(\d+)', url)
    if not match:
        raise ValueError(f"Couldn't parse deck ID from URL: {url}")
    deck_id = match.group(1)
    api_url = f"https://archidekt.com/api/decks/{deck_id}/"
    response = requests.get(api_url)
    response.raise_for_status()
    data = response.json()

    deck_name = data.get('name', 'Unknown_Deck')
    cards = {}
    for card in data.get('cards', []):
        name = card.get('card', {}).get('oracleCard', {}).get('name', '')
        if not name:
            continue
        categories = card.get('categories', [])
        category = categories[0] if categories else 'Uncategorized'
        cards[name.lower()] = {
            'name': name,
            'category': category,
            'categories': categories,
            'entry_id': str(card.get('id', '')),       # deckRelationId — needed for removal
            'card_id': str(card.get('card', {}).get('id', '')),  # cardid
        }
    return deck_id, deck_name, cards


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
            # if not cards:  # print fields from first card only
            #     print(f"  Debug — EDHREC card fields: {list(card.keys())}")
            if key in cards:
                cards[key]['sections'].append(section)
            else:
                inclusion = card.get('inclusion', 0)
                potential = card.get('potential_decks', 0)
                inclusion_pct = inclusion / potential if potential else None
                cards[key] = {
                    'name': name,
                    'inclusion': inclusion_pct,
                    'synergy': card.get('synergy', 0),
                    'trend_zscore': card.get('trend_zscore'),
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
            prices = card.get('prices', {})
            results[name.lower()] = {
                'mana_cost': card.get('mana_cost', ''),
                'type_line': card.get('type_line', ''),
                'oracle_text': card.get('oracle_text', ''),
                'power': card.get('power'),
                'toughness': card.get('toughness'),
                'loyalty': card.get('loyalty'),
                'price_usd': prices.get('usd'),
                'price_usd_foil': prices.get('usd_foil'),
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

        price = sf.get('price_usd')
        price_str = f"${price}" if price else 'N/A'

        rank = f"#{i}  " if show_rank else ""
        print(f"  {rank}{name}  {sf.get('mana_cost', '')}  —  {price_str}")
        print(f"  {sf.get('type_line', '')}" + (f"  —  {stats}" if stats else ''))
        oracle = sf.get('oracle_text', '')
        if oracle:
            print(f"  {wrap_text(oracle, width=70, indent=2)}")
        inclusion_str = f"{card['inclusion']:.0%}" if card['inclusion'] is not None else 'N/A'
        trend = card.get('trend_zscore')
        trend_str = f"{trend:+.2f}" if trend is not None else 'N/A'
        print(f"  Inclusion: {inclusion_str}   Synergy: {card['synergy']:.0%}   Trend: {trend_str}   Sections: {sections}")
        print("-" * 80)


def deck_analysis(archidekt_url, edhrec_url):
    print("\nRunning deck analysis...")

    print("  Fetching deck from Archidekt...")
    try:
        deck_id, deck_name, deck_cards = fetch_archidekt_full(archidekt_url)
    except Exception as e:
        print(f"  Error fetching deck: {e}")
        return
    print(f"  Found {len(deck_cards)} cards in '{deck_name}'.")

    print("  Fetching EDHREC data...")
    try:
        edhrec_cards = fetch_edhrec(edhrec_url)
    except Exception as e:
        print(f"  Error fetching EDHREC data: {e}")
        return
    print(f"  Found {len(edhrec_cards)} cards on EDHREC page.")

    print(f"  Fetching prices from Scryfall for {len(deck_cards)} cards...")
    try:
        scryfall = fetch_scryfall([c['name'] for c in deck_cards.values()])
    except Exception as e:
        print(f"  Warning: Scryfall lookup failed ({e}). Prices will be unavailable.")
        scryfall = {}

    # Merge deck cards with EDHREC and Scryfall data
    enriched = []
    for key, card in deck_cards.items():
        edhrec = edhrec_cards.get(key)
        sf = scryfall.get(key, {})
        price = sf.get('price_usd')
        price_str = f"${price}" if price else '—'
        if edhrec:
            enriched.append({
                'name': card['name'],
                'category': card['category'],
                'inclusion': edhrec['inclusion'],
                'synergy': edhrec['synergy'],
                'trend_zscore': edhrec.get('trend_zscore'),
                'sections': ', '.join(edhrec['sections']),
                'price': price_str,
                'note': '',
            })
        else:
            enriched.append({
                'name': card['name'],
                'category': card['category'],
                'inclusion': None,
                'synergy': None,
                'trend_zscore': None,
                'sections': '',
                'price': price_str,
                'note': 'Not on EDHREC page',
            })

    # Group by category
    categories = {}
    for card in enriched:
        cat = card['category']
        categories.setdefault(cat, []).append(card)

    # Sort each category: EDHREC cards by synergy desc, then non-EDHREC at bottom
    for cat in categories:
        categories[cat].sort(
            key=lambda c: (c['synergy'] is None, -(c['synergy'] or 0))
        )

    # --- Terminal output ---
    print(f"\n{'=' * 80}")
    print(f"  DECK ANALYSIS: {deck_name}")
    print(f"{'=' * 80}")

    for cat_name in sorted(categories.keys()):
        cards = categories[cat_name]
        print(f"\n  ── {cat_name} ({len(cards)} cards) ──")
        print(f"  {'Card':<40} {'Price':>8} {'Inclusion':>10} {'Synergy':>10} {'Trend':>8}  Note")
        print(f"  {'-'*40} {'-'*8} {'-'*10} {'-'*10} {'-'*8}  {'-'*20}")
        for card in cards:
            inclusion = f"{card['inclusion']:.0%}" if card['inclusion'] is not None else '—'
            synergy = f"{card['synergy']:.0%}" if card['synergy'] is not None else '—'
            trend = f"{card['trend_zscore']:+.2f}" if card['trend_zscore'] is not None else '—'
            print(f"  {card['name']:<40} {card['price']:>8} {inclusion:>10} {synergy:>10} {trend:>8}  {card['note']}")

    # --- CSV output ---
    now = datetime.now()
    safe_name = re.sub(r'[^\w\s-]', '', deck_name).strip().replace(' ', '_')
    output_dir = os.path.join('outputs', safe_name)
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f"{safe_name}_{now.strftime('%m_%Y')}.csv")

    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Category', 'Card Name', 'Price (USD)', 'Inclusion', 'Synergy', 'Trend', 'EDHREC Sections', 'Note'])
        for cat_name in sorted(categories.keys()):
            for card in categories[cat_name]:
                inclusion = f"{card['inclusion']:.0%}" if card['inclusion'] is not None else ''
                synergy = f"{card['synergy']:.0%}" if card['synergy'] is not None else ''
                trend = f"{card['trend_zscore']:+.2f}" if card['trend_zscore'] is not None else ''
                writer.writerow([cat_name, card['name'], card['price'], inclusion, synergy, trend, card['sections'], card['note']])

    print(f"\n  Analysis saved to: {filename}")


def prompt_maybeboard(sorted_missing, deck_id, config):
    answer = input("\nWould you like to add cards to your Maybeboard? (y/n): ").strip().lower()
    if answer != 'y':
        return

    print("\nEnter card numbers separated by commas, ranges using ':', or both.")
    print("Examples:  1, 3, 5   |   1:10   |   1:5, 8, 12")
    raw = input("Cards to add: ").strip()

    picks = []
    try:
        for part in raw.split(','):
            part = part.strip()
            if ':' in part:
                start, end = part.split(':', 1)
                picks.extend(range(int(start.strip()), int(end.strip()) + 1))
            elif part:
                picks.append(int(part))
    except ValueError:
        print("Invalid input — please enter numbers and ranges only.")
        return

    selected = []
    seen = set()
    for pick in picks:
        if pick in seen:
            continue
        seen.add(pick)
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


def compare_decklists(archidekt_url_a, archidekt_url_b):
    print("\nComparing decklists...")

    print("  Fetching Deck A from Archidekt...")
    try:
        _, deck_a_name, deck_a = fetch_archidekt_full(archidekt_url_a)
    except Exception as e:
        print(f"  Error fetching Deck A: {e}")
        return
    print(f"  Deck A: '{deck_a_name}' — {len(deck_a)} cards.")

    print("  Fetching Deck B from Archidekt...")
    try:
        _, deck_b_name, deck_b = fetch_archidekt_full(archidekt_url_b)
    except Exception as e:
        print(f"  Error fetching Deck B: {e}")
        return
    print(f"  Deck B: '{deck_b_name}' — {len(deck_b)} cards.")

    # Cards in B but not A
    missing_keys = {k for k in deck_b if k not in deck_a}
    missing_cards = {k: deck_b[k] for k in missing_keys}
    print(f"  Found {len(missing_cards)} cards in '{deck_b_name}' not in '{deck_a_name}'.")

    # Optionally attach EDHREC data
    edhrec_cards = {}
    edhrec_answer = input("\nWould you like to attach EDHREC inclusion and synergy numbers? (y/n): ").strip().lower()
    if edhrec_answer == 'y':
        edhrec_url = input("Enter EDHREC URL: ").strip()
        print("  Fetching EDHREC data...")
        try:
            edhrec_cards = fetch_edhrec(edhrec_url)
            print(f"  Found {len(edhrec_cards)} cards on EDHREC page.")
        except Exception as e:
            print(f"  Warning: Could not fetch EDHREC data ({e}). Continuing without it.")

    # Fetch Scryfall details
    print(f"  Fetching card details from Scryfall for {len(missing_cards)} cards...")
    try:
        scryfall = fetch_scryfall([c['name'] for c in missing_cards.values()])
    except Exception as e:
        print(f"  Warning: Scryfall lookup failed ({e}). Continuing without card details.")
        scryfall = {}

    # Build enriched card list grouped by Deck B's categories
    categories = {}
    for key, card in missing_cards.items():
        edhrec = edhrec_cards.get(key)
        sf = scryfall.get(key, {})
        price = sf.get('price_usd')
        enriched = {
            'name': card['name'],
            'category': card['category'],
            'mana_cost': sf.get('mana_cost', ''),
            'type_line': sf.get('type_line', ''),
            'oracle_text': sf.get('oracle_text', ''),
            'stats': format_stats(sf),
            'price': f"${price}" if price else '—',
            'inclusion': edhrec['inclusion'] if edhrec else None,
            'synergy': edhrec['synergy'] if edhrec else None,
            'trend_zscore': edhrec.get('trend_zscore') if edhrec else None,
            'sections': ', '.join(edhrec['sections']) if edhrec else '',
            'note': '' if edhrec else ('Not on EDHREC page' if edhrec_answer == 'y' else ''),
        }
        categories.setdefault(card['category'], []).append(enriched)

    # Sort each category by synergy desc, non-EDHREC at bottom
    for cat in categories:
        categories[cat].sort(key=lambda c: (c['synergy'] is None, -(c['synergy'] or 0)))

    # --- Terminal output ---
    print(f"\n{'=' * 80}")
    print(f"  DECKLIST COMPARISON")
    print(f"  Cards in '{deck_b_name}' not in '{deck_a_name}' ({len(missing_cards)} cards)")
    print(f"{'=' * 80}")

    rank = 1
    for cat_name in sorted(categories.keys()):
        cards = categories[cat_name]
        print(f"\n  ── {cat_name} ({len(cards)} cards) ──")
        print("=" * 80)
        for card in cards:
            inclusion_str = f"{card['inclusion']:.0%}" if card['inclusion'] is not None else 'N/A'
            synergy_str = f"{card['synergy']:.0%}" if card['synergy'] is not None else 'N/A'
            trend_str = f"{card['trend_zscore']:+.2f}" if card['trend_zscore'] is not None else 'N/A'
            print(f"  #{rank}  {card['name']}  {card['mana_cost']}  —  {card['price']}")
            if card['type_line']:
                print(f"  {card['type_line']}" + (f"  —  {card['stats']}" if card['stats'] else ''))
            if card['oracle_text']:
                print(f"  {wrap_text(card['oracle_text'], width=70, indent=2)}")
            print(f"  Inclusion: {inclusion_str}   Synergy: {synergy_str}   Trend: {trend_str}" +
                  (f"   Sections: {card['sections']}" if card['sections'] else '') +
                  (f"   [{card['note']}]" if card['note'] else ''))
            print("-" * 80)
            rank += 1

    # --- CSV output ---
    now = datetime.now()
    safe_name = re.sub(r'[^\w\s-]', '', deck_a_name).strip().replace(' ', '_')
    output_dir = os.path.join('outputs', safe_name)
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f"{safe_name}_deck_comparison_{now.strftime('%m_%Y')}.csv")

    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Category', 'Card Name', 'Mana Cost', 'Type', 'Price (USD)',
                         'Inclusion', 'Synergy', 'Trend', 'EDHREC Sections', 'Note'])
        rank = 1
        for cat_name in sorted(categories.keys()):
            for card in categories[cat_name]:
                inclusion = f"{card['inclusion']:.0%}" if card['inclusion'] is not None else ''
                synergy = f"{card['synergy']:.0%}" if card['synergy'] is not None else ''
                trend = f"{card['trend_zscore']:+.2f}" if card['trend_zscore'] is not None else ''
                writer.writerow([cat_name, card['name'], card['mana_cost'], card['type_line'],
                                 card['price'], inclusion, synergy, trend, card['sections'], card['note']])
                rank += 1

    print(f"\n  Comparison saved to: {filename}")

    # --- Maybeboard prompt ---
    answer = input(f"\nWould you like to add any of these cards to '{deck_a_name}' Maybeboard? (y/n): ").strip().lower()
    if answer != 'y':
        return

    # Flat list sorted by synergy desc for selection
    all_cards = sorted(
        [c for cat in categories.values() for c in cat],
        key=lambda c: (c['synergy'] is None, -(c['synergy'] or 0))
    )

    print(f"\n  {'#':<5} {'Card':<40} {'Inclusion':>10} {'Synergy':>10}")
    print(f"  {'-'*5} {'-'*40} {'-'*10} {'-'*10}")
    for i, card in enumerate(all_cards, 1):
        inclusion_str = f"{card['inclusion']:.0%}" if card['inclusion'] is not None else 'N/A'
        synergy_str = f"{card['synergy']:.0%}" if card['synergy'] is not None else 'N/A'
        print(f"  #{i:<4} {card['name']:<40} {inclusion_str:>10} {synergy_str:>10}")

    print("\nEnter card numbers separated by commas, ranges using ':', or both.")
    print("Examples:  1, 3, 5   |   1:10   |   1:5, 8, 12")
    raw = input("Cards to add: ").strip()

    picks = []
    try:
        for part in raw.split(','):
            part = part.strip()
            if ':' in part:
                start, end = part.split(':', 1)
                picks.extend(range(int(start.strip()), int(end.strip()) + 1))
            elif part:
                picks.append(int(part))
    except ValueError:
        print("Invalid input — please enter numbers and ranges only.")
        return

    selected = []
    seen = set()
    for pick in picks:
        if pick in seen:
            continue
        seen.add(pick)
        if 1 <= pick <= len(all_cards):
            selected.append(all_cards[pick - 1])
        else:
            print(f"  Skipping #{pick} — out of range.")

    if not selected:
        print("No valid cards selected.")
        return

    print(f"\nCards selected to add to '{deck_a_name}' Maybeboard:")
    for card in selected:
        print(f"  {card['name']}")

    confirm = input("\nConfirm? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        return

    config = load_config()
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

    # Fetch Deck A's ID
    match = re.search(r'/decks/(\d+)', archidekt_url_a)
    deck_a_id = match.group(1)

    print("Adding cards to Maybeboard...")
    succeeded, failed = add_to_maybeboard(deck_a_id, [c['name'] for c in selected], session)

    if succeeded:
        print(f"\nSuccessfully added ({len(succeeded)}):")
        for name in succeeded:
            print(f"  {name}")
    if failed:
        print(f"\nFailed to add ({len(failed)}):")
        for name, status, body in failed:
            print(f"  {name}  →  HTTP {status}: {body}")


def clean_maybeboard(archidekt_url, edhrec_url, threshold=0.0):
    print("\nCleaning Maybeboard...")

    print("  Fetching deck from Archidekt...")
    try:
        deck_id, deck_name, deck_cards = fetch_archidekt_full(archidekt_url)
    except Exception as e:
        print(f"  Error fetching deck: {e}")
        return
    print(f"  Found {len(deck_cards)} cards in '{deck_name}'.")

    # Filter to only Maybeboard cards
    maybeboard = {k: v for k, v in deck_cards.items() if v['category'].lower() == 'maybeboard'}
    if not maybeboard:
        print("  No cards found in Maybeboard.")
        return
    print(f"  Found {len(maybeboard)} cards in Maybeboard.")

    print("  Fetching EDHREC data...")
    try:
        edhrec_cards = fetch_edhrec(edhrec_url)
    except Exception as e:
        print(f"  Error fetching EDHREC data: {e}")
        return
    print(f"  Found {len(edhrec_cards)} cards on EDHREC page.")

    # Determine which cards to remove
    to_remove = []
    for key, card in maybeboard.items():
        edhrec = edhrec_cards.get(key)
        if threshold == 0.0:
            # Remove only cards not on EDHREC
            if not edhrec:
                to_remove.append(card)
        else:
            # Remove cards not on EDHREC or below threshold
            if not edhrec or (edhrec['synergy'] is not None and edhrec['synergy'] < threshold):
                to_remove.append(card)

    if not to_remove:
        print("  No cards meet the removal criteria.")
        return

    # Show what will be removed
    print(f"\n  Cards to be removed from Maybeboard ({len(to_remove)}):\n")
    print(f"  {'Card':<40} {'Synergy':>10}  Reason")
    print(f"  {'-'*40} {'-'*10}  {'-'*25}")
    for card in sorted(to_remove, key=lambda c: c['name']):
        edhrec = edhrec_cards.get(card['name'].lower())
        synergy_str = f"{edhrec['synergy']:.0%}" if edhrec and edhrec['synergy'] is not None else '—'
        reason = 'Not on EDHREC page' if not edhrec else f'Synergy below {threshold:.0%}'
        print(f"  {card['name']:<40} {synergy_str:>10}  {reason}")

    confirm = input(f"\n  Remove these {len(to_remove)} cards? (y/n): ").strip().lower()
    if confirm != 'y':
        print("  Cancelled.")
        return

    config = load_config()
    username = config.get('archidekt', 'username', fallback='').strip()
    password = config.get('archidekt', 'password', fallback='').strip()

    if not username or not password:
        print(f"\n  No credentials found in {CONFIG_FILE}. Please fill in your username and password.")
        return

    print("\n  Logging in to Archidekt...")
    try:
        session = archidekt_login(username, password)
    except Exception as e:
        print(f"  Login failed: {e}")
        return
    print("  Logged in.")

    print("  Removing cards...")
    import time

    # Separate cards missing required IDs
    skipped = [c['name'] for c in to_remove if not c['entry_id'] or not c['card_id']]
    valid = [c for c in to_remove if c['entry_id'] and c['card_id']]

    if skipped:
        print(f"  Skipping {len(skipped)} card(s) with missing IDs: {', '.join(skipped)}")

    # Build one payload with all removals
    cards_payload = [
        {
            'action': 'remove',
            'cardid': card['card_id'],
            'customCardId': None,
            'categories': card['categories'],
            'deckRelationId': card['entry_id'],
            'modifications': {
                'quantity': 1,
                'modifier': 'Normal',
                'customCmc': None,
                'companion': False,
                'flippedDefault': False,
                'label': ',#656565',
            },
            'patchId': random_patch_id(),
        }
        for card in valid
    ]

    if not cards_payload:
        print("  No valid cards to remove.")
        return

    print(f"  Sending batch removal of {len(cards_payload)} card(s)...")
    while True:
        response = session.patch(
            f'https://archidekt.com/api/decks/{deck_id}/modifyCards/v2/',
            json={'cards': cards_payload},
        )
        if response.status_code == 429:
            wait = 61
            try:
                import re as _re
                match = _re.search(r'(\d+) second', response.text)
                if match:
                    wait = int(match.group(1)) + 1
            except Exception:
                pass
            print(f"  Rate limited — waiting {wait}s before retrying batch...")
            time.sleep(wait)
            continue
        break

    if response.ok:
        print(f"\n  Done. Successfully removed {len(valid)} card(s).")
    else:
        print(f"\n  Batch removal failed — HTTP {response.status_code}: {response.text[:200]}")
        print("  No cards were removed.")


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python compare.py <archidekt_url> <edhrec_url>")
        print("  python compare.py --analyze <archidekt_url> <edhrec_url>")
        print("  python compare.py --compare <archidekt_url_a> <archidekt_url_b>")
        print("  python compare.py --clean-maybeboard <archidekt_url> <edhrec_url> [threshold]")
        print("")
        print("  threshold is optional (e.g. 0.10 = remove cards below 10% synergy).")
        print("  If omitted or 0, only cards not on EDHREC are removed.")
        sys.exit(1)

    # Standalone analysis mode
    if sys.argv[1] == '--analyze':
        deck_analysis(sys.argv[2], sys.argv[3])
        return

    # Standalone compare mode
    if sys.argv[1] == '--compare':
        compare_decklists(sys.argv[2], sys.argv[3])
        return

    # Standalone clean maybeboard mode
    if sys.argv[1] == '--clean-maybeboard':
        threshold = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
        clean_maybeboard(sys.argv[2], sys.argv[3], threshold)
        return

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

    answer = input(f"\nHow many cards do you want to see? (1-{len(sorted_missing)}, or 'n' to skip): ").strip().lower()

    displayed = []
    if answer != 'n' and answer != '0':
        try:
            n = int(answer)
            n = max(1, min(n, len(sorted_missing)))
            displayed = sorted_missing[:n]
            print(f"\nTop {n} cards on EDHREC not in your deck (out of {len(missing)}):\n")
            print_cards(displayed, scryfall)
        except ValueError:
            print("Invalid input, skipping card display.")

    prompt_maybeboard(displayed if displayed else sorted_missing, deck_id, config)

    # Deck analysis — last step
    answer = input("\nWould you like to run a deck analysis? (y/n): ").strip().lower()
    if answer == 'y':
        deck_analysis(archidekt_url, edhrec_url)


if __name__ == '__main__':
    main()
