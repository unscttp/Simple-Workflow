# Taxonomy and comparison

## Building the taxonomy

A taxonomy is the backbone of a survey: it decides which works are discussed
together and what the reader learns from the grouping.

1. **List the works** you kept, each with a one-line description of *how* it
   approaches the problem (not what it achieves).
2. **Find the separating dimension.** Look for the design choice that most
   clearly splits the works into groups: what is learned or optimised, where
   knowledge comes from, the architecture family, the stage of a pipeline,
   the supervision signal, the deployment setting. Good dimensions are
   mechanisms, not dates or venues.
3. **Form 3-6 categories** along that dimension. Each category needs at least
   two works; merge or drop categories with one. A second level (4.1.1) is
   fine for a large category, but keep the tree shallow.
4. **Check that the categories are exhaustive and exclusive.** Every kept work
   belongs to exactly one category. If a work genuinely combines two, put it in
   the one that matches its main contribution and mention it in the other.
5. **Name categories by mechanism**, such as "Retrieval-augmented methods" or
   "Rule-based pipelines", not by a single paper's name.

Describe the taxonomy in one paragraph at the start of the section, and, if it
helps, as an indented list or a small tree.

## The comparison table

Pick 4-6 dimensions that matter for choosing between approaches. Typical
dimensions:

| Dimension | Example values |
| --- | --- |
| Category | the taxonomy category |
| Core technique | short phrase |
| Data or supervision | labelled / self-supervised / none |
| Evaluation | benchmark or dataset named in the source |
| Reported result | only as the source states it, with the metric |
| Cost or requirements | compute, data size, human effort |
| Limitations | as stated by the source or by later work |

Rules:

- One row per work, with its citation number in the first column, for example
  `Smith et al. [4]`.
- Write "not reported" rather than guessing a value.
- Do not compare numbers measured on different benchmarks or metrics as if
  they were comparable; say so in the text instead.

After the table, write two or three paragraphs on what it shows: which
trade-offs recur, which categories lack evaluation, where results disagree.

## Challenges and future directions

Each open challenge should point to evidence: a failure mode several sources
report, a setting no source evaluates, or results that conflict. Each future
direction should follow from a challenge. Avoid generic directions ("more data",
"better models") unless a source argues for them specifically.
