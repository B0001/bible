#!/usr/bin/env python
# parse the Bible text
import org.apache.spark.ml.feature.{HashingTF, RegexTokenizer}
import org.apache.spark.sql.functions._
import org.apache.spark.ml.linalg.SparseVector

// upload your vocabulary
// from https://www.ef.edu/english-resources/english-vocabulary/top-100-words/
val yourVocab = Array[String]("a", "about", "all", "also", "and", "as", "at", "be", "because", "but", "by", "can", "come", "could", "day", "do", "even", "find", "first", "for", "from", "get", "give", "go", "have", "he", "her", "here", "him", "his", "how", "I", "if", "in", "into", "it", "its", "just", "know", "like", "look", "make", "man", "many", "me", "more", "my", "new", "no", "not", "now", "of", "on", "one", "only", "or", "other", "our", "out", "people", "say", "see", "she", "so", "some", "take", "tell", "than", "that", "the", "their", "them", "then", "there", "these", "they", "thing", "think", "this", "those", "time", "to", "two", "up", "use", "very", "want", "way", "we", "well", "what", "when", "which", "who", "will", "with", "would", "year", "you", "your").
    mkString(" ").
    toLowerCase + " -- yourvocab"

val site = "https://raw.githubusercontent.com/tushortz/variety-bible-text/master/bibles/nasb.txt"
val txt = scala.io.Source.fromURL(site).mkString + " " + yourVocab
val raw_lines = txt.split("\n")
val lines = raw_lines.filter(_.contains(" -- ")).toSeq.toDF("raw").
    select(
        col("raw"),
        split(col("raw"), " -- ").getItem(0) as "verse",
        split(col("raw"), " -- ").getItem(1) as "ref" )
val my_bucket = "gs://pure-polymer-205710/"
lines.write.mode("overwrite").parquet(my_bucket + "lines")

val tokenizer = new RegexTokenizer().
    setInputCol("verse").
    setOutputCol("words").
    setPattern("[\\p{Punct}\\W]")
val wordsData = tokenizer.transform(lines)

val hashingTF = new HashingTF().
    setInputCol("words").
    setOutputCol("rawFeatures").
    setNumFeatures(100000)

val featurizedData = hashingTF.transform(wordsData)
// alternatively, CountVectorizer can also be used to get term frequency vectors
featurizedData.write.mode("overwrite").parquet(my_bucket + "featurizedData")

val yourVector = featurizedData.filter("ref == 'yourvocab'").select(col("rawFeatures")).collect()(0).getAs[SparseVector](0).toArray
val biblical = featurizedData.filter("ref != 'yourvocab'")

def currentComprensionRate(
        vocab: Seq[Double],
        verse: SparseVector): Double = vocab.
    zip(verse.toArray).
    map{ case (x, y) => x * y }.
    sum / verse.toArray.sum

val ccr = udf(currentComprensionRate _)

val withCcr = biblical.
    withColumn("ccr", ccr(lit(yourVector), col("rawFeatures")))

val knownWordsRate = 0.95
val easyVerses = withCcr.
    filter(col("ccr") >= knownWordsRate).
    select("ref", "verse")

val top10 = withCcr.orderBy(desc("ccr")).limit(10)

// // create a dictionary with reference keys and verse values.
// lines = txt.split('\n')
// splits = [line.split(' -- ') for line in lines]
// verse_ref_dict = dict()
// for line in lines:
//     xy = line.split(' -- ')
//     try:
//         verse_ref_dict[xy[1]] = xy[0]
//     except:
//         pass


// lookup_df = pd.DataFrame(verse_ref_dict.values(), index = verse_ref_dict.keys())
// lookup_df_fname = os.path.join(app_dir, 'lookup_df.csv')
// lookup_df.to_csv(lookup_df_fname)

// // create a global vocabulary list
// from nltk.tokenize import RegexpTokenizer

// tokenizer = RegexpTokenizer(r'\w+')
// tokens = tokenizer.tokenize(txt.lower())
// un_tokens = list(set(tokens))

// // Snowball stemmer
// from nltk.stem.snowball import SnowballStemmer

// print(" ".join(SnowballStemmer.languages))

// // Create a new instance of a language specific subclass.
// stemmer = SnowballStemmer("english", ignore_stopwords=True)

// // Stem words.
// stems = [stemmer.stem(token) for token in tokens]
// unique_stems = list(set(stems))
// srs = pd.Series(unique_stems)
// srs.to_csv('unique_stems.csv', header=False)


// your_vocab_fname = 'my_vocab.txt'
// with open(your_vocab_fname, 'r') as f:
//     your_vocab = f.read()

// your_tokens = tokenizer.tokenize(your_vocab.lower())

// // Find passages with a given percentage of known words.
// // 'Frustration level' refers to over 10% of words in a passage
// // 95% comprehension is ideal for vocabulary growth (cite?).
// // being unknown.
// known_words_rate = 0.95
// passage_min_verse_length = 1
// lookup_df['comprehension_rate'] = 0

// def ratio_generator(row):
//     verse_tokens = tokenizer.tokenize(row.iloc[0].lower())
//     known_token_sum = 0
//     for token in verse_tokens:
//         if token in your_tokens:
//             known_token_sum += 1

//     return known_token_sum / len(verse_tokens)
    
// lookup_df['comprehension_rate_dict'] = lookup_df.apply(lambda verse:

//                                                        , axis=1)

                                                       
// for verse_ref, verse in lookup_df.iterrows():
//     verse_tokens = tokenizer.tokenize(verse.iloc[0].lower())
//     known_token_sum = 0
//     for token in verse_tokens:
//         if token in your_tokens:
//             known_token_sum += 1

//     lookup_df[verse_ref, 'comprehension_rate'] = known_token_sum / len(verse_tokens)


// // NER
