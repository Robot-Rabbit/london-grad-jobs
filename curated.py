"""Hand-checked snapshot of 2027-intake London graduate schemes (checked 6 Oct 2026).

Big employers' schemes (banks, consultancies) rarely appear on Reed/Adzuna, so this
list is merged with the live feed. Edit it as schemes open and close.
"""

SNAPSHOT_DATE = "2026-10-06"
_BASE = "https://www.jointheintake.com/graduate-schemes/"

# (id, employer, title, area, salary_min, salary_label, deadline, url_or_slug)
_ROWS = [
    ("phx-pm", "Phoenix Group", "Investment Analyst, Private Markets", "London", 45000, "£45,000–£60,000", "2026-10-09", "phoenix-group-investment-analyst-private-markets-london-5y1uog"),
    ("wtw-da", "WTW", "Corporate Risk & Broking – Graduate Data Analyst", "London", None, "", "2026-10-09", "wtw-2027-corporate-risk-and-broking-graduate-data-analyst-london-97htul"),
    ("wtw-gdp", "WTW", "Corporate Risk & Broking, Graduate Development Programme", "London", None, "", "2026-10-09", "wtw-2027-corporate-risk-and-broking-graduate-development-programme-london-j3huoq"),
    ("wtw-act", "WTW", "Corporate Risk & Broking – Actuarial Graduate Analyst", "London", None, "", "2026-10-09", "wtw-2027-corporate-risk-and-broking-actuarial-graduate-analyst-london-6ogtel"),
    ("how-act", "Howden", "Trainee Actuarial Consultant – Retirement", "London", None, "", "2026-10-12", "howden-2027-graduate-trainee-actuarial-consultant-retirement-london-glovly"),
    ("mha-aud-jan", "MHA", "Audit Junior ACA Graduate Scheme (January 2027 start)", "City of London (EC2Y)", None, "", "2026-10-12", "mha-audit-junior-aca-graduate-scheme-london-january-2027-london-gcr3e2"),
    ("mha-nfp-jan", "MHA", "Audit Junior (Not-For-Profit) ACA (January 2027 start)", "City of London (EC2Y)", None, "", "2026-10-12", "mha-audit-junior-not-for-profit-aca-graduate-scheme-london-january-2027-london-8ctt63"),
    ("mha-dig-jan", "MHA", "Audit Junior ACA – Digital Assets & Investment Funds (January 2027 start)", "City of London (EC2Y)", None, "", "2026-10-12", "mha-audit-junior-aca-digital-assets-and-investment-funds-graduate-scheme-january-2027-london-r0w3m4"),
    ("mha-data-jan", "MHA", "Data Analytics Junior Level 4/ACA (January 2027 start)", "City of London (EC2Y)", None, "", "2026-10-12", "mha-data-analytics-junior-level-4-aca-graduate-scheme-london-january-2027-london-bnm6xq"),
    ("mha-hca-jan", "MHA", "Human Capital Advisory Tax Associate ATT/CTA (January 2027 start)", "City of London (EC2Y)", None, "", "2026-10-12", "mha-human-capital-advisory-tax-associate-att-cta-gradaute-scheme-london-january-2027-london-rr8zwr"),
    ("how-crs", "Howden", "Credit Risk Solutions Graduate Programme", "City of London (EC3)", 40000, "£40,000", "2026-10-13", "howden-credit-risk-solutions-graduate-programme-2027-london-mbs0eq"),
    ("how-rc", "Howden", "Risk and Compliance Graduate Programme", "City of London (EC3)", 35000, "£35,000", "2026-10-13", "howden-risk-and-compliance-graduate-programme-2027-london-m1sepr"),
    ("how-cpr", "Howden", "Credit & Political Risk Graduate Programme", "City of London (EC3)", 40000, "£40,000", "2026-10-13", "howden-credit-and-political-risk-graduate-programme-2027-london-knue8w"),
    ("how-ltc", "Howden", "Legal, Technical & Claims Graduate Programme", "City of London (EC3)", 35000, "£35,000", "2026-10-13", "howden-legal-technical-and-claims-graduate-programme-2027-london-mlrm3p"),
    ("how-mcl", "Howden", "Marine, Cargo & Logistics Graduate Programme", "City of London (EC3)", None, "", "2026-10-13", "howden-marine-cargo-and-logistics-graduate-programme-2027-london-lht7bt"),
    ("cap-bt", "Capgemini", "Accelerate Graduate Programme – Business Technology", "London", None, "", "2026-10-16", "capgemini-accelerate-graduate-programme-business-technology-london-4xvp7t"),
    ("laz-fa", "Lazard", "Financial Advisory Full-Time Analyst", "London", None, "", "2026-10-17", "lazard-2027-london-financial-advisory-full-time-analyst-london-0bufiw"),
    ("pjt-crp", "PJT Partners", "July Full Time Analyst (Corporate Rotational Programme)", "London", None, "", "2026-10-18", "pjt-partners-2027-july-full-time-analyst-corporate-rotational-programme-london-gyc991"),
    ("wtw-ds", "WTW", "Data Scientist Graduate Programme – Insurance Consulting", "London / Reigate", None, "", "2026-10-19", "wtw-2027-data-scientist-graduate-programme-insurance-consulting-london-reigate-london-odw8jt"),
    ("wtw-pc", "WTW", "Actuarial Insurance Consulting Graduate Programme, Property & Casualty", "London / Reigate / Cambridge", None, "", "2026-10-19", "wtw-2027-actuarial-insurance-consulting-graduate-programme-property-and-casualty-london-reigate-cambridge-london-fxevu3"),
    ("amex-pm", "American Express", "Campus Full Time – Product Manager", "London", None, "", "2026-10-23", "american-express-campus-full-time-product-manager-2027-uk-london-5klx94"),
    ("amex-swe", "American Express", "Campus Full Time – Software Engineer", "London", None, "", "2026-10-23", "american-express-campus-full-time-software-engineer-2027-uk-london-a9qvy2"),
    ("mufg-gcib", "MUFG", "UK Analyst Programme: Global Corporate Investment Banking", "London", None, "", "2026-10-25", "mufg-2027-mufg-uk-analyst-programme-global-corporate-investment-banking-london-1r20yb"),
    ("mufg-cm", "MUFG", "UK Analyst Programme: Capital Markets", "London", None, "", "2026-10-25", "mufg-2027-mufg-uk-analyst-programme-capital-markets-london-l37roh"),
    ("fti-ins", "FTI Consulting", "Graduate Scheme, Global Insurance Services", "London", None, "", "2026-10-30", "fti-consulting-2027-graduate-scheme-global-insurance-services-london-s8r6vm"),
    ("fti-sc", "FTI Consulting", "Graduate Scheme, Strategic Communications", "London", None, "", "2026-10-30", "fti-consulting-2027-graduate-scheme-strategic-communications-london-ra61js"),
    ("fti-efc", "FTI Consulting", "Graduate Scheme, Economic & Financial Consulting", "London", None, "", "2026-10-30", "fti-consulting-2027-graduate-scheme-economic-and-financial-consulting-london-se4gbo"),
    ("fti-df", "FTI Consulting", "Graduate Scheme, Digital Forensics", "London", None, "", "2026-10-30", "fti-consulting-et-2027-graduate-scheme-digital-forensics-london-r72sdk"),
    ("fti-cyber", "FTI Consulting", "Graduate Scheme, Cyber Security", "London", None, "", "2026-10-30", "fti-consulting-2027-graduate-scheme-cyber-security-london-pw812x"),
    ("fti-tax", "FTI Consulting", "Graduate Scheme, Tax Advisory", "London", None, "", "2026-10-30", "fti-consulting-2027-graduate-scheme-tax-advisory-london-sy3npm"),
    ("mac-ia", "Macquarie", "Central Executive Group Graduate Programme – Internal Audit", "London", None, "", "2026-10-30", "macquarie-2027-central-executive-group-graduate-programme-internal-audit-london-iwch7c"),
    ("mac-tech", "Macquarie", "Technology Graduate Programme", "London", None, "", "2026-10-30", "macquarie-2027-technology-graduate-programme-london-kaaho7"),
    ("barc-quant", "Barclays", "Quantitative Analytics Associate Graduate Programme", "Canary Wharf", None, "", "2026-11-01", "barclays-2027-quantitative-analytics-associate-graduate-programme-london-canary-wharf-rfzjz1"),
    ("jpm-ib", "J.P. Morgan", "Global Investment Banking Analyst Program", "London", None, "", "2026-11-01", "j-p-morgan-2027-global-investment-banking-analyst-program-full-time-london-91zvta"),
    ("jpm-cadp", "J.P. Morgan", "Corporate Functions – Corporate Analyst Development Program", "London", None, "", "2026-11-01", "j-p-morgan-2027-corporate-functions-corporate-analyst-development-program-full-time-analyst-program-london-c66txm"),
    ("mha-aud-apr", "MHA", "Audit Junior ACA Graduate Scheme (April 2027 start)", "City of London (EC2Y)", None, "", "2026-12-07", "mha-audit-junior-aca-graduate-scheme-london-april-2027-london-9vzbi1"),
    ("shell-fin", "Shell", "Finance – Shell Graduate Programme", "South Bank", None, "", "2026-12-15", "shell-finance-shell-graduate-programme-2027-united-kingdom-london-yu4ro1"),
    ("shell-idt", "Shell", "Information & Digital Technology (IDT) – Shell Graduate Programme", "South Bank", None, "", "2026-12-15", "shell-information-and-digital-technology-idt-shell-graduate-programme-2027-united-kingdom-london-x675i7"),
    ("mha-aud-sep", "MHA", "Audit Junior ACA Graduate Scheme (September 2027 start)", "City of London (EC2Y)", None, "", "2027-01-11", "mha-audit-junior-aca-graduate-scheme-london-september-2027-london-tepdgf"),
    ("mha-nfp-sep", "MHA", "Audit Junior (Not-For-Profit) ACA (September 2027 start)", "City of London (EC2Y)", None, "", "2027-01-11", "mha-audit-junior-not-for-profit-aca-graduate-scheme-london-september-2027-london-jbp7vc"),
    ("mha-rd-sep", "MHA", "R&D Tax Associate ATT/CTA (September 2027 start)", "City of London (EC2Y)", None, "", "2027-01-11", "mha-r-and-d-tax-associate-att-cta-graduate-scheme-london-september-2027-london-b89nj2"),
    ("mha-ct-sep", "MHA", "Corporate Tax Associate ATT/CTA (September 2027 start)", "City of London (EC2Y)", None, "", "2027-01-11", "mha-corporate-tax-associate-att-cta-graduate-scheme-london-september-2027-london-e4t42s"),
    ("mha-pc-sep", "MHA", "Private Client Tax ACA/CTA (September 2027 start)", "City of London (EC2Y)", None, "", "2027-01-11", "mha-private-client-tax-aca-cta-graduate-scheme-london-september-2027-london-2q0n2y"),
    ("aos-lto", "A&O Shearman", "Law Tech Operations Graduate Programme", "London", 38000, "£38,000 (yr 1), £43,500 (yr 2)", None, "https://www.brightnetwork.co.uk/graduate-jobs/allen-overy/law-tech-operations-graduate-programme-2027"),
    ("roth-ever", "Rothschild & Co", "Evergreen Global Advisory Graduate Programme", "London", None, "", None, "https://www.brightnetwork.co.uk/graduate-jobs/rothschild-co/evergreen-2027-global-advisory-graduate-programme-london"),
]


def curated_jobs():
    jobs = []
    for jid, employer, title, area, sal_min, sal_label, deadline, url in _ROWS:
        jobs.append({
            "id": f"scheme:{jid}",
            "source": "scheme",
            "employer": employer,
            "title": title,
            "area": area,
            "salary_min": sal_min,
            "salary_max": None,
            "salary_label": sal_label,
            "deadline": deadline,
            "posted": None,
            "description": f"2027-intake graduate scheme: {title} at {employer}, London.",
            "url": url if url.startswith("http") else _BASE + url,
        })
    return jobs
