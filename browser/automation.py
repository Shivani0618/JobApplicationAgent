import asyncio
from playwright.async_api import async_playwright

class JobBrowser:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None

    async def start(self, headless=False):
        """Launch web browser."""
        self.playwright_mgr = await async_playwright().start()
        user_data_dir = "./browser_session"

        self.context = await self.playwright_mgr.chromium.launch_persistent_context(
            user_data_dir,
            headless=headless,
            channel="chrome",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ],
            ignore_default_args=["--enable-automation"]
        )
        self.page = self.context.pages[0]
        print("Browser started!")

    async def detect_ats(self, url):
        """Figure out which job site this is."""
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"Navigation warning: {e}")
            await asyncio.sleep(5)

        current_url = self.page.url.lower()

        if "myworkdayjobs.com" in url or "myworkdayjobs.com" in current_url:
            return "Workday"
        elif "greenhouse.io" in url or "greenhouse.io" in current_url:
            return "Greenhouse"
        elif "lever.co" in url or "lever.co" in current_url:
            return "Lever"
        elif "linkedin.com" in current_url:
            return "LinkedIn"
        elif "indeed.com" in current_url:
            return "Indeed"
        elif "unstop.com" in current_url:
            return "Unstop"
        elif "naukri.com" in current_url:
            return "Naukri"
        else:
            return "Unknown"

    async def scrape_job_description(self):
        """Read job posting text."""
        try:
            content = await self.page.evaluate('''() => {
                const selectors = [
                    '.job-details-jobs-unified-top-card__job-insight',
                    '.jobs-description__content',
                    '.jobs-box__html-content',
                    '#job-details',
                    '.job-description',
                    'article',
                    'main'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.trim().length > 100) return el.innerText.trim();
                }
                return document.body.innerText;
            }''')
            return content[:10000] if content else ""
        except Exception as e:
            print(f"Error scraping JD: {e}")
            return ""

    async def check_job_is_open(self):
        """Make sure the position isn't closed before trying."""
        try:
            # Check for closed signs
            closed_text = await self.page.evaluate('''() => {
                const body = document.body.innerText.toLowerCase();
                return (
                    body.includes("no longer accepting applications") ||
                    body.includes("job is closed") ||
                    body.includes("position has been filled") ||
                    body.includes("this job has expired")
                );
            }''')
            if closed_text:
                return False, "Job listing is closed or no longer accepting applications."

            # Look for apply button
            apply_selector = self._get_apply_selector()
            apply_btn = self.page.locator(apply_selector).first
            await apply_btn.wait_for(state="visible", timeout=6000)
            return True, "ok"
        except Exception:
            return False, "No apply button found. Job may be expired or behind a login wall."

    def _get_apply_selector(self):
        return (
            'button:has-text("Easy Apply"):visible, '
            'button[aria-label*="Easy Apply"]:visible, '
            '.jobs-apply-button:visible, '
            '.jobs-apply-button--top-card:visible, '
            'button:has-text("Apply now"):visible, '
            'button:has-text("Apply"):visible, '
            'a:has-text("Apply"):visible'
        )

    async def click_apply_button(self):
        """Click the apply button and switch tabs if needed."""
        try:
            apply_selector = self._get_apply_selector()
            apply_btn = self.page.locator(apply_selector).first
            await apply_btn.wait_for(state="visible", timeout=8000)
            
            async def _handle_new_tab(page_info):
                self.page = await page_info.value
                await self.page.bring_to_front()
                await self.page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(4)
                print("Switched to new tab (external ATS).")

            try:
                # Try clicking normally
                async with self.context.expect_page(timeout=4000) as new_page_info:
                    await apply_btn.click(timeout=4000)
                await _handle_new_tab(new_page_info)
                return True
            except Exception as e:
                err_str = str(e).lower()
                if 'intercept' in err_str:
                    # Push through overlays and banners
                    try:
                        async with self.context.expect_page(timeout=4000) as new_page_info:
                            await apply_btn.click(force=True, timeout=4000)
                        await _handle_new_tab(new_page_info)
                    except Exception as inner_e:
                        if 'timeout' in str(inner_e).lower():
                            await asyncio.sleep(3) # Wait for popup
                        else:
                            raise inner_e
                    return True
                elif 'timeout' in err_str:
                    # Successfully clicked but opening in place
                    await asyncio.sleep(3)
                    return True
                else:
                    raise e
                    
        except Exception as e:
            print(f"Could not click apply button: {e}")
            return False

    async def get_form_fields(self, ats_type="Unknown"):
        """Find the form boxes on the screen."""
        if ats_type == "LinkedIn":
            return await self._get_linkedin_modal_fields()
        else:
            return await self._get_generic_fields()

    async def _get_linkedin_modal_fields(self):
        """Look nicely inside LinkedIn's Apply popup."""
        fields = await self.page.evaluate('''() => {
            // Focus on the apply form, ignore chat
            let modal = document.querySelector('.jobs-easy-apply-modal');
            if (!modal) {
                const dialogs = Array.from(document.querySelectorAll('div[role="dialog"], [data-test-modal]'));
                modal = dialogs.find(d => 
                    d.innerText.toLowerCase().includes('apply') ||
                    d.querySelector('button[aria-label*="Submit application"]')
                ) || dialogs[0];
            }
            
            if (!modal) return [];

            const inputs = Array.from(modal.querySelectorAll('input, textarea, select'));
            
            return inputs.map(input => {
                // Skip stuff we can't type in
                if (input.type === 'hidden' || input.type === 'submit' || input.type === 'button') return null;

                let labelText = '';

                // Find label by ID
                if (input.id) {
                    const lbl = modal.querySelector(`label[for="${CSS.escape(input.id)}"]`);
                    if (lbl) labelText = lbl.innerText.trim();
                }

                // Find by aria attributes
                if (!labelText && input.getAttribute('aria-labelledby')) {
                    const ids = input.getAttribute('aria-labelledby').split(' ');
                    const parts = ids.map(id => {
                        const el = document.getElementById(id);
                        return el ? el.innerText.trim() : '';
                    });
                    labelText = parts.filter(Boolean).join(' ');
                }

                // Find by direct aria label
                if (!labelText && input.getAttribute('aria-label')) {
                    labelText = input.getAttribute('aria-label');
                }

                // Look at placeholder
                if (!labelText && input.placeholder) {
                    labelText = input.placeholder;
                }

                // Check closest headings
                if (!labelText) {
                    let el = input.parentElement;
                    let depth = 0;
                    while (el && depth < 6) {
                        const legend = el.querySelector('legend, h3, h4, label');
                        if (legend && legend.innerText.trim().length > 2) {
                            labelText = legend.innerText.trim().split('\\n')[0];
                            break;
                        }
                        el = el.parentElement;
                        depth++;
                    }
                }

                if (!labelText) labelText = input.name || 'unknown_field';

                // Grab dropdown choices
                let options = [];
                if (input.tagName === 'SELECT') {
                    options = Array.from(input.options).map(o => ({
                        value: o.value,
                        text: o.text.trim()
                    })).filter(o => o.value && o.text);
                }

                return {
                    id: input.id || '',
                    name: input.name || '',
                    type: input.tagName === 'SELECT' ? 'select' : 
                          input.tagName === 'TEXTAREA' ? 'textarea' : 
                          (input.type || 'text'),
                    label: labelText.replace(/\\s+/g, ' ').trim(),
                    placeholder: input.placeholder || '',
                    options: options,
                    required: input.required || input.getAttribute('aria-required') === 'true'
                };
            }).filter(f => f !== null && f.label && f.label !== 'unknown_field');
        }''')
        return fields

    async def _get_generic_fields(self):
        """Scan the screen for standard form fields."""
        fields = await self.page.evaluate('''() => {
            let inputs = Array.from(document.querySelectorAll(
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, select'
            ));
            
            // Peek inside embedded forms
            for (const iframe of iframes) {
                try {
                    const doc = iframe.contentDocument || iframe.contentWindow.document;
                    if (doc) {
                        const frameInputs = Array.from(doc.querySelectorAll(
                            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, select'
                        ));
                        inputs = inputs.concat(frameInputs);
                    }
                } catch(e) {}
            }

            return inputs.map(input => {
                let labelText = '';

                if (input.id) {
                    // Get the nearby label text
                    const docRoot = input.getRootNode();
                    const lbl = docRoot.querySelector(`label[for="${CSS.escape(input.id)}"]`);
                    if (lbl) labelText = lbl.innerText.trim();
                }
                if (!labelText && input.getAttribute('aria-label'))
                    labelText = input.getAttribute('aria-label');
                if (!labelText && input.placeholder)
                    labelText = input.placeholder;
                if (!labelText) {
                    let el = input.parentElement;
                    let d = 0;
                    while (el && d < 5) {
                        const t = (el.innerText || '').split('\\n')[0].trim();
                        if (t.length > 2) { labelText = t; break; }
                        el = el.parentElement; d++;
                    }
                }

                let options = [];
                if (input.tagName === 'SELECT') {
                    options = Array.from(input.options)
                        .map(o => ({ value: o.value, text: o.text.trim() }))
                        .filter(o => o.value);
                }

                return {
                    id: input.id || '',
                    name: input.name || '',
                    type: input.tagName === 'SELECT' ? 'select' :
                          input.tagName === 'TEXTAREA' ? 'textarea' :
                          (input.type || 'text'),
                    label: (labelText || input.name || '').replace(/\\s+/g, ' ').trim(),
                    placeholder: input.placeholder || '',
                    options: options,
                    required: input.required
                };
            }).filter(f => {
                if (!f.label) return false;
                const ignore = ['search', 'keyword', 'find jobs', 'select language', 'recaptcha'];
                return !ignore.some(i => f.label.toLowerCase().includes(i));
            });
        }''')
        return fields

    async def fill_field(self, field, value):
        """Smartly insert the value into the field."""
        if not value:
            return False

        field_type = field.get('type', 'text')
        label = field.get('label', '')

        # Pinpoint the element
        try:
            if field.get('id'):
                locator = self.page.locator(f'#{field["id"]}')
            elif field.get('name'):
                locator = self.page.locator(f'[name="{field["name"]}"]')
            else:
                # Fallback to visual label
                locator = self.page.get_by_label(label, exact=False)

            if field_type in ['text', 'email', 'tel', 'url', 'number', 'textarea']:
                await locator.wait_for(state="visible", timeout=4000)
                await locator.fill(str(value))
                print(f" Filled '{label}' = '{str(value)[:40]}'")

            elif field_type == 'select':
                await locator.wait_for(state="visible", timeout=4000)
                # Pick the best dropdown option
                options = field.get('options', [])
                matched = None
                value_lower = str(value).lower()
                for opt in options:
                    if value_lower in opt['text'].lower() or value_lower == opt['value'].lower():
                        matched = opt['value']
                        break
                if matched:
                    await locator.select_option(value=matched)
                else:
                    # Force selection by text
                    try:
                        await locator.select_option(label=str(value))
                    except:
                        await locator.select_option(index=1)  # Just pick the first choice
                print(f" Selected '{label}' = '{value}'")

            elif field_type == 'radio':
                # Find and click the matching radio dot
                radio_locator = self.page.locator(
                    f'input[type="radio"]'
                ).filter(has=self.page.locator(f'xpath=../label[contains(text(),"{value}")]'))
                if await radio_locator.count() > 0:
                    await radio_locator.first.click()
                else:
                    # Backup click plan
                    lbl = self.page.locator(f'label:has-text("{value}")').first
                    await lbl.click()
                print(f" Radio '{label}' = '{value}'")

            elif field_type == 'checkbox':
                if str(value).lower() in ['yes', 'true', '1', 'checked']:
                    await locator.check()
                    print(f" Checked '{label}'")

            elif field_type == 'file':
                await locator.set_input_files(str(value))
                print(f" Uploaded file for '{label}'")

            return True

        except Exception as e:
            print(f" Could not fill '{label}' (type={field_type}): {e}")
            return False

    async def get_modal_step_info(self):
        """Check application progress."""
        try:
            info = await self.page.evaluate('''() => {
                const progress = document.querySelector(
                    '.artdeco-completeness-meter-linear__bar, [role="progressbar"]'
                );
                const header = document.querySelector(
                    '.jobs-easy-apply-modal__steps-count, [data-test-modal-steps]'
                );
                return {
                    progress_text: header ? header.innerText.trim() : '',
                    progress_value: progress ? progress.getAttribute('aria-valuenow') : null
                };
            }''')
            return info
        except:
            return {}

    async def click_next_or_submit(self):
        """Move forward or submit the form."""
        try:
            # Submit button
            submit = self.page.locator(
                'button[aria-label*="Submit application"], '
                'button:has-text("Submit application"), '
                'button:has-text("Submit"), '
                'input[type="submit"]'
            ).first
            if await submit.is_visible(timeout=2000):
                await submit.click()
                await asyncio.sleep(3)
                return 'submitted'
        except:
            pass

        try:
            # Review step
            review = self.page.locator('button:has-text("Review")').first
            if await review.is_visible(timeout=1000):
                await review.click()
                await asyncio.sleep(2)
                return 'review'
        except:
            pass

        try:
            # Continue onward
            next_btn = self.page.locator(
                'button:has-text("Next"), button:has-text("Continue")'
            ).first
            if await next_btn.is_visible(timeout=1000):
                await next_btn.click()
                await asyncio.sleep(2)
                return 'next'
        except:
            pass

        return 'stuck'

    async def dismiss_modal_if_open(self):
        """Close popups if they stay open."""
        try:
            dismiss = self.page.locator(
                'button[aria-label="Dismiss"], button:has-text("Done"), button:has-text("Close")'
            ).first
            if await dismiss.is_visible(timeout=2000):
                await dismiss.click()
        except:
            pass

    async def shutdown(self):
        if self.context:
            await self.context.close()
            print("Browser closed.")
        if hasattr(self, 'playwright_mgr') and self.playwright_mgr:
            await self.playwright_mgr.stop()
