#!/usr/bin/env python
# parse the Bible text
import requests
import nltk
import os
import pandas as pd

app_dir = '~/bible'

if not os.path.exists(os.path.expanduser('~/nltk_data')):
    nltk.download('stopwords')
    
# # collect the Scriptures as a single string.
# site = 'https://raw.githubusercontent.com/tushortz/variety-bible-text/master/bibles/nasb.txt'
# html = requests.get(site)
# txt = html.text
# lines = txt.split('\n\r')
nasb_fname = '~/Downloads/nasb.txt'
with open(os.path.expanduser(nasb_fname)) as f:
    txt = f.read()
          
# create a dictionary with reference keys and verse values.
lines = txt.split('\n')
splits = [line.split(' -- ') for line in lines]
verse_ref_dict = dict()
for line in lines:
    xy = line.split(' -- ')
    try:
        verse_ref_dict[xy[1]] = xy[0]
    except:
        pass


lookup_df = pd.DataFrame(verse_ref_dict.values(), index = verse_ref_dict.keys())
lookup_df_fname = os.path.join(app_dir, 'lookup_df.csv')
lookup_df.to_csv(lookup_df_fname)

# create a global vocabulary list
from nltk.tokenize import RegexpTokenizer

tokenizer = RegexpTokenizer(r'\w+')
tokens = tokenizer.tokenize(txt.lower())
un_tokens = list(set(tokens))

# Snowball stemmer
from nltk.stem.snowball import SnowballStemmer

print(" ".join(SnowballStemmer.languages))

# Create a new instance of a language specific subclass.
stemmer = SnowballStemmer("english", ignore_stopwords=True)

# Stem words.
stems = [stemmer.stem(token) for token in tokens]
unique_stems = list(set(stems))
srs = pd.Series(unique_stems)
srs.to_csv('unique_stems.csv', header=False)

# upload your vocabulary
your_vocab_fname = 'my_vocab.txt'
with open(your_vocab_fname, 'r') as f:
    your_vocab = f.read()

your_tokens = tokenizer.tokenize(your_vocab.lower())

# Find passages with a given percentage of known words.
# 'Frustration level' refers to over 10% of words in a passage
# 95% comprehension is ideal for vocabulary growth (cite?).
# being unknown.
known_words_rate = 0.95
passage_min_verse_length = 1
lookup_df['comprehension_rate'] = 0

def ratio_generator(row):
    verse_tokens = tokenizer.tokenize(row.iloc[0].lower())
    known_token_sum = 0
    for token in verse_tokens:
        if token in your_tokens:
            known_token_sum += 1

    return known_token_sum / len(verse_tokens)
    
lookup_df['comprehension_rate_dict'] = lookup_df.apply(lambda verse:

                                                       , axis=1)

                                                       
for verse_ref, verse in lookup_df.iterrows():
    verse_tokens = tokenizer.tokenize(verse.iloc[0].lower())
    known_token_sum = 0
    for token in verse_tokens:
        if token in your_tokens:
            known_token_sum += 1

    lookup_df[verse_ref, 'comprehension_rate'] = known_token_sum / len(verse_tokens)


# NER
