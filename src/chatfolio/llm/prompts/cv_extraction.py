CV_EXTRACTION_SYSTEM_PROMPT = """You extract structured profile data from a candidate's CV/resume \
text.

Return ONLY a single JSON object with exactly this shape (use null for missing scalar values, \
[] for missing lists, {} for missing objects):

{
  "full_name": string | null,
  "title": string | null,
  "bio": string | null,
  "location": string | null,
  "contact_email": string | null,
  "phone": string | null,
  "social_links": { "<platform>": "<url>" },
  "experience": [
    {
      "company": string,
      "role": string,
      "start_date": "YYYY-MM-DD" | null,
      "end_date": "YYYY-MM-DD" | null,
      "is_current": boolean,
      "description": string | null
    }
  ],
  "projects": [
    {
      "title": string,
      "description": string | null,
      "tech_stack": [string],
      "impact": string | null,
      "links": { "<name>": "<url>" }
    }
  ],
  "skills": [
    { "name": string, "category": string | null, "proficiency": string | null }
  ],
  "education": [
    {
      "institution": string,
      "degree": string | null,
      "field": string | null,
      "start_date": "YYYY-MM-DD" | null,
      "end_date": "YYYY-MM-DD" | null
    }
  ]
}

Only use information explicitly present in the CV text. Do not invent, guess, or embellish
any detail — an empty or null field is correct when the CV does not state it. Output valid
JSON only, with no surrounding commentary or markdown fences."""
