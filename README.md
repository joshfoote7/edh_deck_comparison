# venv set up
cd "C:\Users\Josh Foote\_All\Projects\EDH Comparisons"
venv\Scripts\activate

# Code to run the decklist to EDHREC comparison
python compare.py <archidekt link> <edhrec link>

# Code to run the deck analysis
python compare.py --analyze <archidekt link> <edhrec link>

# Code to run deck comparison
python compare.py --compare <archidekt link 1> <achidekt link 2>

# Code to clean up maybeboard
python compare.py --clean-maybeboard <archidekt link> <edhrec link>
python compare.py --clean-maybeboard <archidekt link> <edhrec link> <synergy_threshold>
