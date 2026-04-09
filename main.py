import asyncio
from agent.graph import app

async def run_agent():
    print("=" * 60)
    print("  AI Job Application Agent")
    print("=" * 60)

    processed = 0

    while True:
        print(f"\n[Queue Pass #{processed + 1}]")

        inputs = {
            "job_id":             None,
            "job_url":            "",
            "company_name":       "",
            "ats_type":           "",
            "candidate_data":     {},
            "custom_answers":     {},
            "unanswered_fields":  [],
            "fail_reason":        "",
            "application_status": "starting",
        }

        final_state = await app.ainvoke(inputs)
        final_status = final_state.get("application_status", "unknown")

        if final_status in ["empty_queue", "starting", "done"] and not final_state.get("job_id"):
            print("\n✅ Queue is empty. All jobs processed.")
            break

        processed += 1
        company = final_state.get("company_name", "Unknown")
        url = final_state.get("job_url", "")

        print(f"\n── Result ──────────────────────────────────────────────")
        print(f"  Company : {company}")
        print(f"  URL     : {url}")
        print(f"  Status  : {final_status}")

        if final_state.get("fail_reason"):
            print(f"  Reason  : {final_state['fail_reason']}")

        if final_state.get("unanswered_fields"):
            print(f"  Unanswered fields logged (add to custom_answers):")
            for f in final_state["unanswered_fields"]:
                print(f"    - {f}")

        # Brief pause to cool down
        await asyncio.sleep(3)

    print(f"\n{'='*60}")
    print(f"  Total jobs processed this run: {processed}")
    print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(run_agent())