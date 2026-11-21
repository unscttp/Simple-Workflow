# Survey pipeline

Use this graph to run the research part of an academic survey as four
reviewed workflow stages, after the scope is agreed and a report folder is
approved:

1. **scope** - turns the conversation into research questions, scope and
   search queries;
2. **collect** - searches the web and keeps a screened, numbered source list;
3. **organise** - builds the taxonomy, comparison table and outline;
4. **write** - writes the survey with numbered citations and saves it as a
   Markdown report.

Each stage sees only the earlier stages' results. The research stages can
only search and read; only the last stage can save reports. The graph returns
`survey.scope`, `survey.sources`, `survey.outline` and `survey.report`
(the saved file name and abstract).
