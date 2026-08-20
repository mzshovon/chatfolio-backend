============ CHATFOLIO REQUIREMENTS =============

# Project

## Name

Chatfolio

## Founder

Mohammad Moniruzzaman

## Product Type

AI-powered SaaS portfolio platform for software and technology professionals.

## Pilot Goal

Launch a minimal but complete Chatfolio pilot where a candidate can create an AI-powered portfolio from a CV or a simple manual profile builder, publish a public Chatfolio page, and allow recruiters to chat with an AI representative that speaks like the candidate using only candidate-approved data.

# Product Vision

Chatfolio transforms a traditional static portfolio into an intuitive, interactive, and conversational portfolio experience.

Instead of making recruiters manually scan a CV, portfolio website, GitHub, LinkedIn, and project descriptions, Chatfolio gives each candidate a public AI portfolio page. Recruiters can browse structured portfolio sections and ask natural questions, similar to an early screening conversation with the candidate.

The system should feel simple for candidates, useful for recruiters, and reliable enough to become a SaaS product.

# Real World Problem

Traditional portfolios and CVs are static. They often fail to answer the exact questions a recruiter has, such as:

- What projects has this candidate built?
- What technologies has the candidate used in real work?
- Which experience is most relevant to my role?
- Is this candidate suitable for frontend, backend, full-stack, AI, DevOps, or another tech role?
- What impact did the candidate create in previous work?
- How can I quickly understand the candidate without scheduling a first call?

Candidates also repeatedly explain the same background, skills, projects, expectations, and experience to different recruiters.

Chatfolio solves this by turning the candidate profile into a structured, searchable, AI-powered portfolio that can answer recruiter questions in real time.

# Target Market

## Pilot Audience

The initial pilot will focus on software and technology professionals to ensure strong benchmarking and reliable AI behavior.

Example candidate categories:

- Frontend developers
- Backend developers
- Full-stack developers
- Mobile app developers
- DevOps engineers
- AI/ML engineers
- Data engineers
- QA engineers
- UI/UX engineers or product-focused technical profiles

## Future Audience

Other professions can be added gradually after the tech-focused pilot is validated.

# User Roles

## Candidate

The candidate is the portfolio owner.

Candidate capabilities:

- Sign up and log in
- Upload CV
- Build a simple profile manually
- Review AI-extracted profile information
- Edit profile, experience, projects, skills, education, and contact details
- Generate public-facing portfolio content
- Publish or unpublish Chatfolio
- Publish Chatfolio with a shareable public URL or subdomain
- Prepare custom domain connection for future SaaS use
- Share Chatfolio anywhere so any recruiter or visitor can open it and chat instantly
- View recruiter conversations and recruiter-provided context

## Recruiter or Visitor

The recruiter is the public visitor who reviews a candidate.

Recruiter capabilities:

- Visit a candidate's public Chatfolio without login
- Browse structured portfolio sections
- Start chatting immediately
- Ask questions about skills, experience, projects, education, and suitability
- Optionally share company or hiring context during the chat
- Use contact actions provided by the candidate

## Admin

The admin manages platform-level operations.

Admin capabilities:

- View users and published Chatfolios
- Monitor CV parsing and AI generation quality
- Review chat usage and system health
- Manage templates and default prompts
- Moderate abusive or unsafe usage
- Prepare the product for future billing and SaaS scaling

# Core Product Principle

Chatfolio must use a candidate-approved AI profile.

No public Chatfolio should be published until the candidate reviews and confirms the generated information. The AI chat must not invent experience, skills, salary, availability, employment status, project impact, education, or personal details that are not available in the candidate-approved profile.

# Complete Product Journey

Chatfolio should be developed as a complete CMS to backend to frontend product journey.

## CMS to Backend

1. Candidate enters profile data through the dashboard, CV upload, or manual builder.
2. CMS sends candidate data and CV files to backend APIs.
3. Backend stores uploaded files and profile draft data.
4. Backend runs CV parsing and LLM extraction.
5. Backend returns structured sections to the CMS for candidate review.
6. Candidate edits and approves the generated data.
7. Backend stores approved data as the source of truth.
8. Backend creates embeddings from approved candidate content.
9. Backend stores embeddings in the vector database.

## Backend to Frontend

1. Candidate publishes the Chatfolio.
2. Backend exposes a public read-only portfolio API by slug, public URL, or subdomain.
3. Public frontend renders the candidate profile from approved data.
4. Recruiter starts a public chat session.
5. Chat frontend sends recruiter questions to the backend.
6. Backend classifies intent, retrieves relevant vector context, and generates a grounded answer.
7. Frontend displays the AI response in a natural candidate-like style.
8. Backend stores messages and recruiter context.
9. Candidate dashboard shows conversation history and useful recruiter metadata.

## Frontend Experience Loop

1. Candidate can continuously update profile data.
2. Backend re-generates affected sections when requested.
3. Candidate approves changes.
4. Embeddings are refreshed.
5. Public Chatfolio and chat answers stay aligned with the latest approved profile.

# Candidate Journey

1. Candidate signs up or logs in.
2. Candidate creates a new Chatfolio profile.
3. Candidate uploads a CV or uses the simple manual profile builder.
4. Backend parses the CV and extracts raw content.
5. LLM converts the raw content into structured profile data.
6. System generates draft portfolio sections:
   - Introduction
   - Address and contact card
   - Experience
   - Impactful projects
   - Skills
   - Education
   - Career summary
7. Candidate reviews, edits, approves, and saves the profile.
8. System embeds approved content into the vector database.
9. Candidate publishes the public Chatfolio page.
10. Recruiters visit the public page and start chatting.
11. Candidate can review recruiter conversations and company context later.

# Recruiter Journey

1. Recruiter opens a candidate's public Chatfolio URL.
2. Recruiter sees a clean, structured portfolio page.
3. Recruiter can browse:
   - Candidate introduction
   - Current role or professional headline
   - Skills
   - Experience
   - Projects
   - Education
   - Contact actions
4. Recruiter starts chatting without login.
5. AI responds in first person as the candidate's virtual representative.
6. During the conversation, the AI politely asks for company or hiring context when useful.
7. Recruiter can continue asking role-fit questions.
8. Candidate can later view the conversation and recruiter context.

# AI Chat Behavior

The AI should speak like the candidate in first person.

Example:

"I have worked with React, Node.js, MongoDB, and AWS across production-level projects."

The AI must remain grounded in approved candidate data.

If the answer is not available, it should respond honestly.

Example:

"I do not have that information in my profile yet, but you can contact me directly for details."

The AI should be professional, concise, and recruiter-friendly. It should avoid overclaiming and should not pretend to have live human awareness.

# Recruiter Context Collection

Recruiters should not be forced to log in before chatting.

However, the AI can politely ask for context at the beginning or middle of the chat.

Example:

"Before I go deeper, may I know which company or team you are hiring for? That will help me share the most relevant parts of my background."

Collected recruiter context may include:

- Recruiter name
- Company name
- Hiring role
- Required skills
- Experience expectation
- Location or remote preference
- Hiring timeline

This information should be stored as chat metadata and shown to the candidate.

# CMS and Dashboard Requirements

The CMS/dashboard is the candidate's control center.

## Candidate Dashboard

Required features:

- Profile overview
- Chatfolio publish status
- Public Chatfolio URL or subdomain
- CV upload status
- AI generation status
- Recruiter conversation summary
- Basic usage summary

## CV Upload

Required features:

- Upload PDF or DOC/DOCX CV
- Validate file type and size
- Show parsing progress
- Show parsing errors when extraction fails
- Allow candidate to retry upload

## Simple Manual Builder

The pilot should support a simple manual builder.

Required fields:

- Full name
- Professional title
- Short bio
- Location
- Email
- Phone or optional contact link
- Social links
- Skills
- Work experience
- Projects
- Education

The manual builder should stay simple for the pilot. It should focus on collecting enough data for a useful public Chatfolio and chat experience.

## AI Generated Content Review

Required features:

- Show generated introduction
- Show extracted experience
- Show extracted projects
- Show extracted skills
- Show extracted education
- Allow edit before approval
- Allow regenerate for selected sections if possible
- Require candidate approval before publish

## Chat Management

Required features:

- View recruiter conversations
- View recruiter company/context if collected
- View conversation timestamp
- Mark conversations as reviewed
- Future-ready structure for notifications

## Portfolio Settings

Required features:

- Publish/unpublish Chatfolio
- Edit public slug
- Generate a shareable public Chatfolio link
- Support a candidate-specific subdomain pattern for sharing
- Prepare custom domain connection settings for future rollout
- Configure contact CTA
- Configure downloadable CV visibility
- Basic theme or layout setting if time allows

# Backend Requirements

## Authentication and User Management

Required features:

- Candidate registration
- Candidate login
- Secure password handling or provider-based auth
- Candidate profile ownership
- Protected dashboard routes
- Public read-only Chatfolio routes by slug, public URL, or subdomain

## CV Processing Pipeline

Required flow:

1. Receive uploaded CV.
2. Store original file securely.
3. Extract text from CV.
4. Send extracted text to LLM structuring pipeline.
5. Store raw parsed result.
6. Store structured draft profile data.
7. Allow candidate review and edits.
8. Mark approved content as source of truth.

## Profile and Portfolio API

Required capabilities:

- Create profile
- Update profile
- Get profile by owner
- Get public profile by slug
- Resolve public Chatfolio by slug, subdomain, or custom domain
- Save generated sections
- Publish/unpublish profile
- Manage experience, projects, skills, education, contact details

## AI and RAG Pipeline

Required components:

- Intent classification
- Candidate data retrieval
- Vector embedding generation
- Vector database storage
- Similarity search
- Response generation
- Grounding and hallucination control
- Conversation history storage

## Chat API

Required capabilities:

- Start public recruiter chat
- Send recruiter message
- Retrieve relevant candidate context
- Generate AI response in first person
- Store chat messages
- Store recruiter metadata when provided
- Rate limit public chat usage
- Prevent abusive or spam usage

## Admin APIs

Required capabilities:

- View users
- View Chatfolio records
- View usage metrics
- View chat counts
- Review failed CV parsing jobs
- Prepare moderation workflow

# Frontend Requirements

## Candidate Frontend

Required screens:

- Sign up
- Login
- Dashboard
- CV upload
- Manual profile builder
- AI-generated profile review
- Edit profile sections
- Portfolio settings
- Recruiter conversations

## Public Chatfolio Frontend

Required sections:

- Candidate introduction
- Professional headline
- Contact card
- Skills
- Experience
- Projects
- Education
- Chat interface
- Contact CTA

The public page should feel minimal, premium, fast, and easy to scan.

## Recruiter Chat Experience

Required behavior:

- Chat should be visible and easy to start
- Recruiter should not need login
- AI responses should feel natural and candidate-like
- Chat should support role-fit questions
- Chat should ask recruiter context politely when useful
- Chat should clearly handle unknown information

# Public URL, Subdomain, and Domain Requirements

The candidate must be able to publish and share Chatfolio anywhere so anyone with the link can open the portfolio and start chatting without login.

## MVP Sharing Model

Required features:

- Generate a public Chatfolio URL after publish
- Support a candidate-owned slug such as `/c/{candidate-slug}`
- Support a candidate-specific subdomain pattern such as `{candidate-slug}.chatfolio.com`
- Validate slug or subdomain uniqueness
- Allow candidate to edit the public slug before or after publish
- Redirect old public URLs when a slug changes if possible
- Keep unpublished Chatfolios inaccessible from public URLs and subdomains

## Future Custom Domain Model

The system should be architected so custom domains can be added later.

Future custom domain capabilities:

- Candidate connects a personal domain or subdomain
- Backend verifies domain ownership
- System provides DNS instructions
- Public Chatfolio resolves from the custom domain
- SSL certificate provisioning is handled automatically
- Candidate can remove or replace a custom domain

# LLM Requirements

## LLM Tasks

The system should use LLMs for:

- CV text understanding
- Structured information extraction
- Portfolio section generation
- Project impact summarization
- Skills grouping
- CV improvement suggestion
- Recruiter intent classification
- RAG response generation
- Follow-up question generation

## Intent Types

Initial recruiter intent categories:

- Skill inquiry
- Project inquiry
- Experience inquiry
- Education inquiry
- Role fit inquiry
- Availability or expectation inquiry
- Contact request
- General introduction
- Unknown or unsupported question

## Guardrails

The AI must:

- Use only approved candidate data
- Avoid hallucinated claims
- Avoid answering private information unless candidate approved it
- Avoid making promises on behalf of the candidate
- Avoid fake salary, availability, notice period, or current employment claims
- Say when information is not available
- Recommend contacting the candidate when needed

# Data Model Overview

Initial entities:

- User
- CandidateProfile
- UploadedCV
- Experience
- Project
- Skill
- Education
- PortfolioSection
- PublicChatfolio
- PublicDomain
- VectorEmbedding
- ChatSession
- ChatMessage
- RecruiterMetadata
- AdminAuditLog

# Security and Privacy

Required considerations:

- Candidate owns their profile data
- Public page exposes only approved information
- Uploaded CV files should be stored securely
- Recruiter chat should be rate limited
- Sensitive candidate data should not be exposed unless approved
- Admin access should be protected
- Chat logs should be visible only to the candidate owner and authorized admins
- The system should prepare for future data deletion/export requests

# SaaS Readiness

The pilot will be free for everyone.

Billing is not required in the MVP, but the architecture should allow billing to be introduced later.

Future SaaS capabilities:

- Free and paid plans
- Usage limits
- Custom domains
- Premium themes
- Advanced recruiter analytics
- Higher AI chat limits
- Team/company accounts
- Candidate lead management

# MVP Scope

The pilot MVP must include:

- Candidate authentication
- CV upload
- Simple manual builder
- CV parsing
- AI structured extraction
- AI-generated portfolio sections
- Candidate review and approval
- Vector embedding storage
- Public Chatfolio page
- Shareable public URL or candidate subdomain
- Public recruiter chat without login
- AI first-person candidate representative
- Recruiter company/context collection during chat
- Candidate dashboard
- Recruiter conversation history
- Publish/unpublish control
- Basic admin visibility

# Phase 2 Scope

Possible Phase 2 features:

- Billing and subscription plans
- Multiple portfolio themes
- Custom domains
- Advanced analytics
- Recruiter lead scoring
- Email notifications
- AI CV improvement suggestions
- Interview simulation mode
- Recruiter-side candidate comparison
- ATS-style job matching
- More professions outside software and tech
- Video or voice-based portfolio interaction
- Integrations with LinkedIn, GitHub, job boards, or calendar tools

# Success Metrics

Pilot success should be measured by:

- Number of candidates who create a Chatfolio
- Percentage of candidates who publish after upload
- CV parsing accuracy
- Number of recruiter chats per public profile
- Average recruiter chat length
- Number of recruiter context captures
- Candidate satisfaction with generated sections
- Recruiter satisfaction with answer quality
- Hallucination or unsupported answer rate
- System cost per candidate and per chat

# Development Priorities

1. Build the complete candidate-to-public-page journey.
2. Ensure the AI only uses candidate-approved data.
3. Make recruiter chat instant and login-free.
4. Keep the dashboard simple but complete.
5. Focus on software and tech candidate benchmarking.
6. Keep billing out of MVP but design the system to support it later.
7. Prioritize reliability, clarity, and trust over excessive feature count.

# Open Questions

These questions can be finalized during implementation:

- Which vector database will be used? - Chroma
- Which LLM provider and model will be used for extraction and chat? - Initially starts with deepseek but should have gpt, gemini, claude, grok, openrouter setup on top of config level
- What file size limit should be allowed for CV upload? - Max 20 MB
- Should downloadable CV be public by default or candidate-controlled? - Public
- Should recruiter context collection be optional or prompted in every chat? - Optional
- What admin moderation level is required for the pilot? - Anything better you suggest.
