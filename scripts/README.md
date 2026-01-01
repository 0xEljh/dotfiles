# Time Accounting

This is my personal system of collecting and aggregating activity tracking data from multiple sources into Notion, creating a centralised (and automated) daily journal of how I spend my time.

The goal is to reduce the friction of accounting for my time to near zero, and to also remove the guesswork from my time accounting.

I run all these scripts here via a simple cronjob.

## Data Sources → Notion

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────────────────┐
│   WakaTime      │────▶│                 │     │  Notion Time Accounting DB  │
│   (coding)      │     │                 │────▶│  - Minutes Coding (number)  │
├─────────────────┤     │   sync scripts  │     │  - Tasks (relation)         │
│  ActivityWatch  │────▶│   (cron)        │────▶│  - Hourly breakdown (table) │
│  (screen time)  │     │                 │     │                             │
├─────────────────┤     │                 │     ├─────────────────────────────┤
│   Bread Tasks   │────▶│                 │────▶│       Bread Tasks DB        │
│   (Notion DB)   │     └─────────────────┘     │   (completed task links)    │
└─────────────────┘                             └─────────────────────────────┘
```
