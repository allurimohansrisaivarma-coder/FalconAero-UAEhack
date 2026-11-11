# FalconAero – Idea/Submission page draft (hackathon.ae → Idea)

## Explain your idea/technical proposal in one sentence (max 100 chars)
FalconAero: AI decision support that re-plans runway slots and stands during fog and disruptions

## Idea/technical proposal final brief & description
FalconAero is an AI-powered decision-support system that helps an Airport Operations Centre recover from disruptions such as fog, storms, runway closures, airspace closures and emergencies.

It ingests operational events (schedules, ETAs, METAR/TAF weather, runway, stand and taxiway status, ATFM caps, NOTAMs), checks the approved plan against them, and forecasts demand against capacity for the next six hours so that problems are flagged before they occur.

Our key innovation is a unified recovery engine. A constraint optimiser plans runway slots and stands together, with taxi routes and times taken into account, so that a change to one resource is checked against the others. Emergencies are planned first and cannot be delayed to help other traffic. The controller sees up to three alternative plans next to a rule-based plan, with predicted delays, diversions and a plain-language reason for every change. Each plan is also stress-tested against the worst case in the weather forecast.

The prototype runs on a simulator with 23 disruption scenarios modelled on real events, and can use live OMDB weather. Recovery plans are never applied without approval, and all clearances remain with authorised aviation personnel. FalconAero supports the UAE's vision for AI-driven aviation excellence by making recovery faster, more consistent and explainable.

## Technology
Artificial Intelligence

## Idea Uniqueness Points
1. Unified recovery engine: runway slots and stands are solved in one model, with taxi routes and times included, instead of one resource at a time.
2. Early warning: the approved plan is re-checked on every event and a six-hour demand/capacity forecast flags runway and stand shortfalls before they happen.
3. Emergencies first: an emergency's slot is fixed before anything else is optimised.
4. Alternatives, side by side: up to three plans against a rule-based plan that obeys the same rules. Diversions are shown next to delay so no plan can look better by dropping flights.
5. Explainable: every change has a reason drawn from the plan data, and each plan is stress-tested against the forecast's worst case.
6. Human in control: plans need approval or rejection, both logged and reversible. Automatic routine upkeep is limited and can be switched off.

## Sources / data
- Live OMDB METAR/TAF: NOAA Aviation Weather Center (aviationweather.gov)
- Traffic patterns and fleet mix: OpenSky Network (ADS-B), Bayanat (UAE aviation statistics), Dubai Pulse
- Operating rules: public ICAO / SKYbrary guidance (LVP, ILS categories), GCAA publications
- Traffic in the demo is simulated. Stand occupancy and A-CDM data are not public and would come from the airport operator and ANSP in a pilot.

## Still to add
- Pitch deck / technical proposal (upload)
- 2–3 minute pitch video link
