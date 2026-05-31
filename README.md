#venv set up
cd "C:\Users\Josh Foote\_All\Projects\EDH Comparisons"
venv\Scripts\activate

#Code to run the decklist to EDHREC comparison
python compare.py <archidekt link> <edhrec link>

#Code to run the deck comparison
python compare.py --analyze <archidekt link> <edhrec link>
