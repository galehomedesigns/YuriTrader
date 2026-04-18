#!/usr/bin/env python3
"""Run procurement enrichment: categorize missing tenders and detect duplicates."""

import os
import sys
import json
from datetime import datetime, timezone
from scripts.crawl import (
    supabase_get, supabase_post, supabase_patch, categorize, HEADERS, SUPABASE_URL, SUPABASE_KEY
)

def categorize_missing_tenders():
    """Assign categories to tenders missing category field."""
    print("\n=== Step 1: Categorizing missing tenders ===")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: Supabase credentials not available")
        return 0
    
    # Get tenders missing category
    tenders = supabase_get('tenders?select=id,title,category&category=is.null')
    print(f"Found {len(tenders)} tenders without category")
    
    if not tenders:
        print("No tenders need categorization")
        return 0
    
    categorized = 0
    categories_count = {}
    
    for tender in tenders:
        title = tender.get('title', '')
        category = categorize(title)
        
        # Update the tender with category
        supabase_patch(
            'tenders',
            {'category': category},
            f'id=eq.{tender["id"]}'
        )
        
        categorized += 1
        categories_count[category] = categories_count.get(category, 0) + 1
        print(f"  Categorized: {title[:60]} -> {category}")
        
        if categorized >= 50:  # Limit output
            print("  ... (hiding more output)")
            break
    
    print(f"\nCategorized {categorized} tenders:")
    for cat, count in sorted(categories_count.items()):
        print(f"  {cat}: {count}")
    
    return categorized

def detect_duplicates():
    """Flag duplicates: same title + organization + closing_date."""
    print("\n=== Step 2: Detecting duplicates ===")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: Supabase credentials not available")
        return 0, []
    
    # Get all tenders
    tenders = supabase_get('tenders?select=id,title,organization,closing_date,url,status')
    print(f"Scanning {len(tenders)} tenders for duplicates")
    
    if not tenders:
        return 0, []
    
    # Group by (title, organization, closing_date)
    seen = {}
    duplicates = []
    
    for tender in tenders:
        title = tender.get('title', '').strip().lower()
        org = tender.get('organization', '').strip().lower()
        closing = tender.get('closing_date', '')
        
        if not title or not closing:
            continue
        
        key = (title, org, closing)
        
        if key in seen:
            # Found a duplicate
            original = seen[key]
            duplicates.append({
                'original_id': original['id'],
                'original_title': original.get('title', ''),
                'duplicate_id': tender['id'],
                'duplicate_title': tender.get('title', ''),
                'organization': org,
                'closing_date': closing,
                'original_url': original.get('url', ''),
                'duplicate_url': tender.get('url', '')
            })
        else:
            seen[key] = tender
    
    # Mark duplicates in database (add a flag)
    if duplicates:
        for dup in duplicates:
            supabase_patch(
                'tenders',
                {'is_duplicate': True, 'duplicate_of_id': dup['original_id']},
                f'id=eq.{dup["duplicate_id"]}'
            )
    
    print(f"\nFound {len(duplicates)} duplicate tenders:")
    for i, dup in enumerate(duplicates[:10]):
        print(f"  [{i+1}] '{dup['duplicate_title'][:50]}...'")
        print(f"      Org: {dup['organization']}, Date: {dup['closing_date']}")
        print(f"      Original: {dup['original_id']}, Duplicate: {dup['duplicate_id']}")
    
    if len(duplicates) > 10:
        print(f"  ... and {len(duplicates) - 10} more duplicates")
    
    return len(duplicates), duplicates

def main():
    """Run the full enrichment workflow."""
    print(f"\n{'='*60}")
    print("PROCUREMENT ENRICHMENT RUN")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("\nERROR: Supabase credentials not configured")
        sys.exit(1)
    
    # Step 1: Categorize missing tenders
    categorized = categorize_missing_tenders()
    
    # Step 2: Detect duplicates
    duplicate_count, duplicates = detect_duplicates()
    
    # Step 3: Get final stats
    print("\n=== Step 3: Final Database Stats ===")
    stats = supabase_get('tenders?select=id,status,category&select=*')
    
    total_tenders = len(stats)
    with_category = len([t for t in stats if t.get('category')])
    open_tenders = len([t for t in stats if t.get('status') == 'open'])
    
    print(f"Total tenders in database: {total_tenders}")
    print(f"Tenders with category: {with_category}")
    print(f"Open tenders: {open_tenders}")
    
    # Generate report
    print("\n" + "="*60)
    print("ENRICHMENT REPORT")
    print("="*60)
    print(f"Date: {datetime.now(timezone.utc).date()}")
    print(f"Time: {datetime.now(timezone.utc).time()}")
    print("-"*60)
    print(f"Tenders categorized: {categorized}")
    print(f"Duplicates found: {duplicate_count}")
    print(f"Total tenders: {total_tenders}")
    print(f"Open tenders: {open_tenders}")
    print("="*60)
    
    # Save report to file
    report = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'tenders_categorized': categorized,
        'duplicates_found': duplicate_count,
        'total_tenders': total_tenders,
        'open_tenders': open_tenders,
        'duplicates': duplicates[:100]  # Limit to first 100
    }
    
    report_path = f'/home/tonygale/openclaw/skills/procurement/reports/enrichment_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.json'
    os.makedirs('/home/tonygale/openclaw/skills/procurement/reports', exist_ok=True)
    
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\nReport saved to: {report_path}")

if __name__ == "__main__":
    main()
