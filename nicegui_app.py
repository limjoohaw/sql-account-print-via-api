"""NiceGUI web application — all pages and UI logic."""

import time
import collections
from nicegui import ui, app, run

from version import APP_NAME, APP_VERSION, APP_BUILD_NUMBER
from shared import CLR_PRIMARY, CLR_SECONDARY, CLR_ACCENT, CLR_DANGER, FONT_IMPORT, status_banner
from auth import (
    find_user, verify_password, create_session_token, validate_session_token,
    hash_password, create_user, update_user, delete_user, load_users,
    SESSION_COOKIE_NAME, User,
)
from companies import (
    load_companies, save_companies, find_company, add_company, update_company,
    delete_company, get_companies_for_user, Company,
)
from doc_types import (
    load_doc_types, get_templates_for_doc_type, parse_report_designer_excel,
    convert_uploaded_templates,
)
from sql_api import SQLAccAPIClient, get_field_value
from logger import print_logger
from config import settings


# ---------------------------------------------------------------------------
# Login rate limiter — max 5 attempts per IP per 60 seconds
# ---------------------------------------------------------------------------
_login_attempts: dict[str, list[float]] = collections.defaultdict(list)
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 60


def _check_rate_limit(ip: str) -> bool:
    """Return True if this IP is rate-limited (too many login attempts)."""
    now = time.time()
    # Clean old entries
    _login_attempts[ip] = [t for t in _login_attempts[ip]
                           if now - t < _LOGIN_WINDOW_SECONDS]
    return len(_login_attempts[ip]) >= _MAX_LOGIN_ATTEMPTS


def _record_login_attempt(ip: str):
    """Record a failed login attempt for rate limiting."""
    _login_attempts[ip].append(time.time())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_current_user() -> User | None:
    """Read session cookie → validate → return User or None."""
    token = app.storage.user.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    username = validate_session_token(token)
    if not username:
        return None
    return find_user(username)


def _require_login():
    """Redirect to /login if not authenticated. Call at top of each page."""
    user = _get_current_user()
    if user is None:
        ui.navigate.to('/login')
        return None
    return user


def _require_admin():
    """Redirect if not admin."""
    user = _require_login()
    if user and not user.is_admin:
        ui.navigate.to('/')
        return None
    return user


def _get_user_companies(user: User) -> list:
    """Get companies accessible by user. Admins see all companies."""
    if user.is_admin:
        return load_companies()
    return get_companies_for_user(user.companies)


def _apply_theme():
    """Apply shared theme — Quasar colors + font."""
    ui.colors(primary=CLR_PRIMARY, secondary=CLR_SECONDARY, accent=CLR_ACCENT)
    ui.add_body_html(FONT_IMPORT)


def _header(user: User | None = None):
    """Shared header bar across all authenticated pages."""
    with ui.header().classes('items-center justify-between px-4 py-2') \
            .style(f'background-color: {CLR_PRIMARY}'):
        ui.label(f'{APP_NAME} v{APP_VERSION}').classes('text-white text-lg font-bold')
        with ui.row().classes('items-center gap-4'):
            if user:
                ui.label(user.username).classes('text-sm opacity-80')
                ui.button('Print', on_click=lambda: ui.navigate.to('/')) \
                    .props('flat dense size=sm color=white')
                ui.button('Settings', on_click=lambda: ui.navigate.to('/settings')) \
                    .props('flat dense size=sm color=white')
                if user.is_admin:
                    ui.button('Admin', on_click=lambda: ui.navigate.to('/admin')) \
                        .props('flat dense size=sm color=white')
                ui.button('Change Password', on_click=lambda: ui.navigate.to('/change-password')) \
                    .props('flat dense size=sm color=white')
                ui.button('Logout', on_click=_logout) \
                    .props('flat dense size=sm color=white')


def _logout():
    """Clear session and redirect to login."""
    app.storage.user[SESSION_COOKIE_NAME] = ''
    ui.navigate.to('/login')


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------

@ui.page('/login')
def page_login():
    _apply_theme()

    # If already logged in, redirect
    if _get_current_user():
        ui.navigate.to('/')
        return

    with ui.column().classes('absolute-center items-center gap-6'):
        ui.label(APP_NAME).classes('text-2xl font-bold').style(f'color: {CLR_PRIMARY}')
        ui.label(f'v{APP_VERSION}').classes('text-xs text-gray-400 -mt-4')

        with ui.card().classes('w-80 p-6'):
            username_input = ui.input('Username').classes('w-full').props('outlined dense')
            password_input = ui.input('Password', password=True, password_toggle_button=True) \
                .classes('w-full').props('outlined dense')

            error_container = ui.column().classes('w-full')

            async def do_login():
                error_container.clear()
                uname = username_input.value.strip()
                pwd = password_input.value

                # Rate limiting
                client_ip = app.storage.user.get('__ip__', 'unknown')
                if _check_rate_limit(client_ip):
                    status_banner(error_container,
                                  'Too many login attempts. Try again in 60 seconds.', 'error')
                    print_logger.warning(f"Rate-limited login attempt: ip={client_ip}")
                    return

                if not uname or not pwd:
                    status_banner(error_container, 'Please enter username and password.', 'warning')
                    return

                user = find_user(uname)
                if not user or not verify_password(pwd, user.password_hash):
                    _record_login_attempt(client_ip)
                    print_logger.warning(f"Failed login: username={uname}, ip={client_ip}")
                    status_banner(error_container, 'Invalid username or password.', 'error')
                    return

                token = create_session_token(user.username)
                app.storage.user[SESSION_COOKIE_NAME] = token
                ui.navigate.to('/')

            ui.button('Login', on_click=do_login) \
                .classes('w-full mt-2').style(f'background-color: {CLR_PRIMARY} !important')

            # Allow Enter key to submit
            password_input.on('keydown.enter', do_login)


# ---------------------------------------------------------------------------
# Main print page
# ---------------------------------------------------------------------------

@ui.page('/')
def page_main():
    _apply_theme()
    user = _require_login()
    if not user:
        return

    _header(user)

    doc_types = load_doc_types()
    user_companies = _get_user_companies(user)
    default_catalog = None  # Loaded lazily

    # Persisted selections — survive page reload / app switch on mobile
    stored = app.storage.user
    saved_company = stored.get('last_company')
    saved_doctype = stored.get('last_doctype')
    saved_format = stored.get('last_format')

    # State refs
    company_select = None
    doctype_select = None
    format_select = None
    docno_input = None
    status_container = None
    _updating = False  # Guard against cascading on_change recursion

    def _get_selected_company() -> Company | None:
        if not company_select or not company_select.value:
            return None
        return find_company(company_select.value)

    def _save_selections():
        """Persist current selections so they survive page reload / app switch."""
        if _updating:
            return
        stored['last_company'] = company_select.value if company_select else None
        stored['last_doctype'] = doctype_select.value if doctype_select else None
        stored['last_format'] = format_select.value if format_select else None

    def _populate_formats(dt_key: str, restore_format: str = None):
        """Populate format dropdown for a doc type. Optionally restore a saved value."""
        nonlocal default_catalog
        if not format_select:
            return

        if not dt_key or dt_key not in doc_types:
            format_select.set_options([])
            format_select.value = None
            return

        dt = doc_types[dt_key]
        company = _get_selected_company()
        company_templates = company.templates if company else None

        if default_catalog is None:
            from doc_types import load_default_templates
            default_catalog = load_default_templates()

        templates = get_templates_for_doc_type(dt, company_templates, default_catalog)

        # Sort: names starting with digits first, then alphabetical
        def _sort_key(t):
            name = t['name']
            starts_with_digit = not name[0].isdigit() if name else True
            return (starts_with_digit, name.lower())

        templates.sort(key=_sort_key)

        options = {}
        for t in templates:
            engine_tag = f" [{t.get('engine', '?')}]" if t.get('engine') else ""
            display = f"{t['name']}{engine_tag}"
            options[t['name']] = display

        format_select.set_options(options)
        if restore_format and restore_format in options:
            format_select.value = restore_format
        elif options:
            format_select.value = list(options.keys())[0]
        else:
            format_select.value = None

    def _on_company_change(e):
        """When company changes, reset doc type and format."""
        nonlocal _updating
        if _updating:
            return
        _updating = True
        if doctype_select:
            doctype_select.value = None
        if format_select:
            format_select.set_options([])
            format_select.value = None
        _updating = False
        _save_selections()

    def _on_doctype_change(e):
        """When doc type changes, populate format dropdown."""
        nonlocal _updating
        if _updating:
            return
        _updating = True
        _populate_formats(doctype_select.value if doctype_select else None)
        _updating = False
        _save_selections()

    async def do_print():
        """Print button handler — fetch JSON → extract dockey → fetch PDF → download."""
        if not company_select.value:
            status_banner(status_container, 'Please select a company.', 'warning')
            return
        if not doctype_select.value:
            status_banner(status_container, 'Please select a document type.', 'warning')
            return
        if not format_select.value:
            status_banner(status_container, 'Please select a format.', 'warning')
            return
        doc_no = docno_input.value.strip()
        if not doc_no:
            status_banner(status_container, 'Please enter a document number.', 'warning')
            return

        company = _get_selected_company()
        if not company:
            status_banner(status_container, 'Company not found.', 'error')
            return

        dt = doc_types[doctype_select.value]
        template_name = format_select.value

        status_banner(status_container, f'Fetching {dt.label} {doc_no}...', 'warning')
        start_time = time.time()

        try:
            # Step 1: Fetch JSON to get dockey
            client = SQLAccAPIClient(
                host=company.api_host,
                region=settings.sqlacc_aws_region,
                access_key=company.access_key,
                secret_key=company.secret_key,
            )
            data = await run.io_bound(client.fetch_document_json, dt.resource, doc_no)

            dockey = get_field_value(data, "dockey")
            if dockey is None:
                latency = int((time.time() - start_time) * 1000)
                status_banner(status_container, f'Document not found: {doc_no}', 'warning')
                print_logger.log_print(user.username, company.name, dt.label,
                                       doc_no, template_name, "NOT_FOUND",
                                       latency_ms=latency)
                return

            # Step 2: Fetch PDF
            response = await run.io_bound(
                client.fetch_document_pdf, dt.resource, dockey, template_name
            )

            pdf_bytes = response.content
            latency = int((time.time() - start_time) * 1000)

            # Validate it's actually a PDF
            if not pdf_bytes or not pdf_bytes[:5] == b'%PDF-':
                status_banner(status_container,
                              'PDF generation failed — response is not a valid PDF. '
                              'Possible server-side issue.', 'error')
                print_logger.log_print(user.username, company.name, dt.label,
                                       doc_no, template_name, "INVALID_PDF",
                                       latency_ms=latency,
                                       error="Response not %PDF-")
                return

            # Step 3: Download to browser
            filename = dt.filename.format(docno=doc_no)
            ui.download(pdf_bytes, filename)
            status_banner(status_container, f'PDF downloaded: {filename}', 'success')
            print_logger.log_print(user.username, company.name, dt.label,
                                   doc_no, template_name, "OK",
                                   latency_ms=latency)

        except Exception as ex:
            latency = int((time.time() - start_time) * 1000)
            error_msg = str(ex)

            # Map specific errors to user-friendly messages
            if 'ConnectionError' in type(ex).__name__ or 'Timeout' in type(ex).__name__:
                banner_msg = 'Cannot reach SQL API service — is the server running?'
            elif '403' in error_msg:
                banner_msg = 'API signature rejected — check Access Key / Secret Key'
            elif '404' in error_msg:
                banner_msg = f'Not found — template "{template_name}" may be incorrect'
            else:
                banner_msg = 'An unexpected error occurred. Check the server logs for details.'

            status_banner(status_container, banner_msg, 'error')
            print_logger.log_print(user.username, company.name, dt.label,
                                   doc_no, template_name, "ERROR",
                                   latency_ms=latency, error=error_msg)

    # --- Build the UI ---
    with ui.column().classes('w-full max-w-lg mx-auto mt-8 gap-4 px-4'):
        with ui.card().classes('w-full p-6'):
            ui.label('Print Document').classes('text-lg font-bold mb-2')

            # Company dropdown
            with ui.column().classes('w-full gap-0'):
                ui.label('Company').classes('text-sm font-medium')
                company_options = {c.id: c.name for c in user_companies}
                company_select = ui.select(
                    options=company_options,
                    on_change=_on_company_change,
                ).classes('w-full').props('outlined dense')

            # Doc Type dropdown
            with ui.column().classes('w-full gap-0'):
                ui.label('Document Type').classes('text-sm font-medium')
                dt_options = {k: v.label for k, v in doc_types.items()}
                doctype_select = ui.select(
                    options=dt_options,
                    on_change=_on_doctype_change,
                ).classes('w-full').props('outlined dense')

            # Format dropdown
            with ui.column().classes('w-full gap-0'):
                ui.label('Format').classes('text-sm font-medium')
                format_select = ui.select(options={}, on_change=lambda _: _save_selections()) \
                    .classes('w-full').props('outlined dense')

            # Doc No input
            with ui.column().classes('w-full gap-0'):
                ui.label('Document No').classes('text-sm font-medium')
                docno_input = ui.input(placeholder='e.g. IV-00001') \
                    .classes('w-full').props('outlined dense')

            # Print button — rightmost convention
            with ui.row().classes('w-full justify-end mt-2'):
                ui.button('Print PDF', on_click=do_print) \
                    .style(f'background-color: {CLR_PRIMARY} !important')

            # Status area
            status_container = ui.column().classes('w-full mt-2')

        # Enter key triggers print
        docno_input.on('keydown.enter', do_print)

        # Restore saved selections (company → doc type → format cascade)
        _updating = True
        if saved_company and saved_company in company_options:
            company_select.value = saved_company
            if saved_doctype and saved_doctype in dt_options:
                doctype_select.value = saved_doctype
                _populate_formats(saved_doctype, restore_format=saved_format)
        _updating = False


# ---------------------------------------------------------------------------
# Change Password page
# ---------------------------------------------------------------------------

@ui.page('/change-password')
def page_change_password():
    _apply_theme()
    user = _require_login()
    if not user:
        return

    _header(user)

    with ui.column().classes('w-full max-w-md mx-auto mt-8 gap-4 px-4'):
        with ui.card().classes('w-full p-6'):
            ui.label('Change Password').classes('text-lg font-bold mb-2')

            current_pw = ui.input('Current Password', password=True, password_toggle_button=True) \
                .classes('w-full').props('outlined dense')
            new_pw = ui.input('New Password', password=True, password_toggle_button=True) \
                .classes('w-full').props('outlined dense')
            confirm_pw = ui.input('Confirm New Password', password=True, password_toggle_button=True) \
                .classes('w-full').props('outlined dense')

            status_ctr = ui.column().classes('w-full')

            def do_change():
                status_ctr.clear()
                if not verify_password(current_pw.value, user.password_hash):
                    status_banner(status_ctr, 'Current password is incorrect.', 'error')
                    return
                if len(new_pw.value) < 8:
                    status_banner(status_ctr, 'New password must be at least 8 characters.', 'warning')
                    return
                if new_pw.value != confirm_pw.value:
                    status_banner(status_ctr, 'New passwords do not match.', 'warning')
                    return

                update_user(user.username, password=new_pw.value)
                status_banner(status_ctr, 'Password changed successfully.', 'success')
                current_pw.value = ''
                new_pw.value = ''
                confirm_pw.value = ''

            with ui.row().classes('w-full justify-end mt-2'):
                ui.button('Cancel', on_click=lambda: ui.navigate.to('/')) \
                    .props(f'flat color={CLR_PRIMARY}')
                ui.button('Save', on_click=do_change) \
                    .style(f'background-color: {CLR_PRIMARY} !important')


# ---------------------------------------------------------------------------
# Settings page — template upload & management (any logged-in user)
# ---------------------------------------------------------------------------

@ui.page('/settings')
def page_settings():
    _apply_theme()
    user = _require_login()
    if not user:
        return

    _header(user)

    doc_types = load_doc_types()
    user_companies = _get_user_companies(user)

    with ui.column().classes('w-full max-w-2xl mx-auto mt-8 gap-4 px-4'):
        ui.label('Template Settings').classes('text-lg font-bold')

        # Company selector
        company_options = {c.id: c.name for c in user_companies}
        company_select = ui.select(
            options=company_options,
            label='Select Company',
        ).classes('w-64').props('outlined dense')

        status_ctr = ui.column().classes('w-full')
        templates_display = ui.column().classes('w-full')

        def _refresh_template_display():
            """Show current templates for selected company."""
            templates_display.clear()
            if not company_select.value:
                return
            company = find_company(company_select.value)
            if not company:
                return

            with templates_display:
                if not company.templates:
                    ui.label('No templates uploaded yet. Upload a Report Designer Excel below.') \
                        .classes('text-sm text-gray-500 italic')
                else:
                    for dt_key, tpl_list in company.templates.items():
                        dt = doc_types.get(dt_key)
                        label = dt.label if dt else dt_key
                        with ui.expansion(f'{label} ({len(tpl_list)} formats)').classes('w-full'):
                            for t in tpl_list:
                                name = t['name'] if isinstance(t, dict) else t
                                engine = t.get('engine', '?') if isinstance(t, dict) else '?'
                                ui.label(f"  {name} [{engine}]").classes('text-xs font-mono')

        company_select.on('update:model-value', lambda _: _refresh_template_display())

        ui.separator()

        # Upload section
        ui.label('Upload Report Designer Excel').classes('text-sm font-bold text-gray-600')
        ui.label(
            'In SQL Account: Tools → Report Designer → Field Chooser → Select All → '
            'Right Click grid header → Grid Export → Export To Microsoft Excel 2007'
        ).classes('text-xs text-gray-400')

        async def handle_upload(e):
            if not company_select.value:
                status_banner(status_ctr, 'Please select a company first.', 'warning')
                return

            try:
                content = await e.file.read()
                uploaded = parse_report_designer_excel(content)
                converted = convert_uploaded_templates(uploaded, doc_types)

                company = find_company(company_select.value)
                if company:
                    company.templates = converted
                    companies = load_companies()
                    for i, c in enumerate(companies):
                        if c.id == company.id:
                            companies[i] = company
                            break
                    save_companies(companies)

                total = sum(len(v) for v in converted.values())
                status_banner(status_ctr,
                              f'Uploaded {total} templates across {len(converted)} document types.',
                              'success')
                _refresh_template_display()

            except Exception as ex:
                status_banner(status_ctr, f'Error parsing Excel: {ex}', 'error')

        ui.upload(
            label='Choose .xlsx file',
            on_upload=handle_upload,
            auto_upload=True,
        ).props('accept=.xlsx').classes('w-full')

        # Manual add section
        ui.separator()
        ui.label('Manual Add Template').classes('text-sm font-bold text-gray-600')

        with ui.row().classes('w-full items-end gap-2'):
            dt_options = {k: v.label for k, v in doc_types.items()}
            manual_dt = ui.select(options=dt_options, label='Doc Type') \
                .classes('flex-1').props('outlined dense')
            manual_name = ui.input(label='Template Name', placeholder='e.g. Sales Invoice 8 (SST 1)') \
                .classes('flex-1').props('outlined dense')
            manual_engine = ui.select(
                options={'FR3': 'FR3 (FastReport)', 'RTM': 'RTM (Report Builder)'},
                label='Engine', value='FR3',
            ).classes('w-32').props('outlined dense')

            def do_manual_add():
                if not company_select.value:
                    status_banner(status_ctr, 'Please select a company first.', 'warning')
                    return
                if not manual_dt.value or not manual_name.value:
                    status_banner(status_ctr, 'Please fill in doc type and template name.', 'warning')
                    return

                company = find_company(company_select.value)
                if not company:
                    return

                dt_key = manual_dt.value
                if dt_key not in company.templates:
                    company.templates[dt_key] = []

                # Check duplicate
                existing_names = [
                    t['name'] if isinstance(t, dict) else t
                    for t in company.templates[dt_key]
                ]
                if manual_name.value.strip() in existing_names:
                    status_banner(status_ctr, 'Template name already exists.', 'warning')
                    return

                company.templates[dt_key].append({
                    'name': manual_name.value.strip(),
                    'engine': manual_engine.value,
                    'built_in': False,
                })

                companies = load_companies()
                for i, c in enumerate(companies):
                    if c.id == company.id:
                        companies[i] = company
                        break
                save_companies(companies)

                status_banner(status_ctr, f'Added template: {manual_name.value.strip()}', 'success')
                manual_name.value = ''
                _refresh_template_display()

            ui.button('Add', on_click=do_manual_add) \
                .style(f'background-color: {CLR_PRIMARY} !important')


# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------

@ui.page('/admin')
def page_admin():
    _apply_theme()
    user = _require_admin()
    if not user:
        return

    _header(user)

    with ui.column().classes('w-full max-w-4xl mx-auto mt-8 gap-6 px-4'):
        # --- User Management ---
        ui.label('User Management').classes('text-lg font-bold')

        users_container = ui.column().classes('w-full')

        def _refresh_users():
            users_container.clear()
            with users_container:
                users = load_users()
                all_companies = load_companies()
                company_names = {c.id: c.name for c in all_companies}

                for u in users:
                    with ui.card().classes('w-full p-3'):
                        with ui.row().classes('w-full items-center justify-between'):
                            with ui.column().classes('gap-0'):
                                with ui.row().classes('items-center gap-2'):
                                    ui.label(u.username).classes('font-bold')
                                    if u.is_admin:
                                        ui.badge('Admin', color='purple').classes('text-xs')
                                companies_str = ', '.join(
                                    company_names.get(cid, cid) for cid in u.companies
                                ) or 'No companies assigned'
                                ui.label(companies_str).classes('text-xs text-gray-500')
                            with ui.row().classes('gap-1'):
                                ui.button(icon='edit', on_click=lambda u=u: _edit_user_dialog(u)) \
                                    .props(f'flat dense size=sm color={CLR_PRIMARY}')
                                ui.button(icon='delete', on_click=lambda u=u: _delete_user_confirm(u)) \
                                    .props(f'flat dense size=sm color={CLR_DANGER}')

        def _edit_user_dialog(target_user: User):
            all_companies = load_companies()
            company_opts = {c.id: c.name for c in all_companies}

            with ui.dialog() as dlg, ui.card().classes('w-96 p-4'):
                ui.label(f'Edit User: {target_user.username}').classes('text-lg font-bold')

                co_select = ui.select(
                    options=company_opts,
                    value=target_user.companies,
                    label='Assigned Companies',
                    multiple=True,
                ).classes('w-full').props('outlined dense')

                is_admin_check = ui.checkbox('Admin', value=target_user.is_admin)

                new_pw = ui.input('New Password (leave blank to keep)', password=True) \
                    .classes('w-full').props('outlined dense')

                edit_status = ui.column().classes('w-full')

                def save_edit():
                    kwargs = {
                        'companies': co_select.value or [],
                        'is_admin': is_admin_check.value,
                    }
                    if new_pw.value:
                        if len(new_pw.value) < 8:
                            status_banner(edit_status, 'Password must be at least 8 characters.', 'warning')
                            return
                        kwargs['password'] = new_pw.value

                    update_user(target_user.username, **kwargs)
                    ui.notify(f'User {target_user.username} updated.', type='positive')
                    dlg.close()
                    _refresh_users()

                with ui.row().classes('w-full justify-end gap-2 mt-2'):
                    ui.button('Cancel', on_click=dlg.close).props(f'flat color={CLR_PRIMARY}')
                    ui.button('Save', on_click=save_edit) \
                        .style(f'background-color: {CLR_PRIMARY} !important')

            dlg.open()

        def _delete_user_confirm(target_user: User):
            with ui.dialog() as dlg, ui.card().classes('p-4'):
                ui.label(f'Delete user "{target_user.username}"?').classes('font-bold')
                ui.label('This action cannot be undone.').classes('text-sm text-gray-500')
                with ui.row().classes('w-full justify-end gap-2 mt-4'):
                    ui.button('Cancel', on_click=dlg.close).props(f'flat color={CLR_PRIMARY}')
                    ui.button('Delete', on_click=lambda: _do_delete_user(dlg, target_user)) \
                        .props(f'outline color={CLR_DANGER}')
            dlg.open()

        def _do_delete_user(dlg, target_user):
            delete_user(target_user.username)
            ui.notify(f'User {target_user.username} deleted.', type='warning')
            dlg.close()
            _refresh_users()

        _refresh_users()

        # Add user form
        ui.separator()
        ui.label('Create New User').classes('text-sm font-bold text-gray-600')

        all_companies_for_form = load_companies()
        co_opts_form = {c.id: c.name for c in all_companies_for_form}

        with ui.row().classes('w-full items-end gap-2 flex-wrap'):
            new_username = ui.input(label='Username').classes('w-40').props('outlined dense')
            new_password = ui.input(label='Password', password=True).classes('w-40').props('outlined dense')
            new_companies = ui.select(
                options=co_opts_form,
                label='Companies',
                multiple=True,
            ).classes('w-64').props('outlined dense')
            new_is_admin = ui.checkbox('Admin')

            def do_create_user():
                if not new_username.value or not new_password.value:
                    ui.notify('Username and password are required.', type='warning')
                    return
                if len(new_password.value) < 8:
                    ui.notify('Password must be at least 8 characters.', type='warning')
                    return
                try:
                    create_user(
                        username=new_username.value.strip(),
                        password=new_password.value,
                        companies=new_companies.value or [],
                        is_admin=new_is_admin.value,
                    )
                    ui.notify(f'User {new_username.value} created.', type='positive')
                    new_username.value = ''
                    new_password.value = ''
                    new_companies.value = []
                    new_is_admin.value = False
                    _refresh_users()
                except ValueError as ex:
                    ui.notify(str(ex), type='negative')

            ui.button('Create', on_click=do_create_user) \
                .style(f'background-color: {CLR_PRIMARY} !important')

        # --- Company Management ---
        ui.separator()
        ui.label('Company Management').classes('text-lg font-bold')

        companies_container = ui.column().classes('w-full')

        def _refresh_companies():
            companies_container.clear()
            with companies_container:
                for c in load_companies():
                    with ui.card().classes('w-full p-3'):
                        with ui.row().classes('w-full items-center justify-between'):
                            with ui.column().classes('gap-0'):
                                ui.label(c.name).classes('font-bold')
                                ui.label(f'Host: {c.api_host}').classes('text-xs text-gray-500')
                                tpl_count = sum(len(v) for v in c.templates.values())
                                ui.label(f'Templates: {tpl_count}').classes('text-xs text-gray-400')
                            with ui.row().classes('gap-1'):
                                ui.button(icon='edit', on_click=lambda c=c: _edit_company_dialog(c)) \
                                    .props(f'flat dense size=sm color={CLR_PRIMARY}')
                                ui.button(icon='delete', on_click=lambda c=c: _delete_company_confirm(c)) \
                                    .props(f'flat dense size=sm color={CLR_DANGER}')

        def _edit_company_dialog(target_company: Company):
            with ui.dialog() as dlg, ui.card().classes('w-96 p-4'):
                ui.label(f'Edit Company').classes('text-lg font-bold')

                ed_name = ui.input('Company Name', value=target_company.name) \
                    .classes('w-full').props('outlined dense')
                ed_host = ui.input('API Host', value=target_company.api_host) \
                    .classes('w-full').props('outlined dense')
                _MASKED = '••••••••'
                ed_ak = ui.input('Access Key', value=_MASKED,
                                 password=True, password_toggle_button=True) \
                    .classes('w-full').props('outlined dense')
                ed_sk = ui.input('Secret Key', value=_MASKED,
                                 password=True, password_toggle_button=True) \
                    .classes('w-full').props('outlined dense')
                ui.label('Leave key fields as •••••••• to keep existing values.') \
                    .classes('text-xs text-gray-400 italic')

                def save_co():
                    new_ak = ed_ak.value.strip()
                    new_sk = ed_sk.value.strip()
                    kwargs = dict(
                        name=ed_name.value.strip(),
                        api_host=ed_host.value.strip(),
                    )
                    # Only update keys if admin typed a new value
                    if new_ak and new_ak != _MASKED:
                        kwargs['access_key'] = new_ak
                    if new_sk and new_sk != _MASKED:
                        kwargs['secret_key'] = new_sk
                    update_company(target_company.id, **kwargs)
                    ui.notify(f'Company updated.', type='positive')
                    dlg.close()
                    _refresh_companies()

                with ui.row().classes('w-full justify-end gap-2 mt-2'):
                    ui.button('Cancel', on_click=dlg.close).props(f'flat color={CLR_PRIMARY}')
                    ui.button('Save', on_click=save_co) \
                        .style(f'background-color: {CLR_PRIMARY} !important')

            dlg.open()

        def _delete_company_confirm(target_company: Company):
            with ui.dialog() as dlg, ui.card().classes('p-4'):
                ui.label(f'Delete company "{target_company.name}"?').classes('font-bold')
                ui.label('This will remove all template configurations for this company.') \
                    .classes('text-sm text-gray-500')
                with ui.row().classes('w-full justify-end gap-2 mt-4'):
                    ui.button('Cancel', on_click=dlg.close).props(f'flat color={CLR_PRIMARY}')
                    ui.button('Delete', on_click=lambda: _do_delete_co(dlg, target_company)) \
                        .props(f'outline color={CLR_DANGER}')
            dlg.open()

        def _do_delete_co(dlg, target_company):
            delete_company(target_company.id)
            ui.notify(f'Company {target_company.name} deleted.', type='warning')
            dlg.close()
            _refresh_companies()

        _refresh_companies()

        # Add company form
        ui.separator()
        ui.label('Add New Company').classes('text-sm font-bold text-gray-600')

        with ui.column().classes('w-full gap-2'):
            with ui.row().classes('w-full gap-2'):
                add_co_id = ui.input(label='Company ID (unique key)', placeholder='e.g. company_a') \
                    .classes('flex-1').props('outlined dense')
                add_co_name = ui.input(label='Company Name', placeholder='e.g. Company A Sdn Bhd') \
                    .classes('flex-1').props('outlined dense')
            with ui.row().classes('w-full gap-2'):
                add_co_host = ui.input(label='API Host', placeholder='e.g. 203.0.113.50') \
                    .classes('flex-1').props('outlined dense')
                add_co_ak = ui.input(label='Access Key') \
                    .classes('flex-1').props('outlined dense')
                add_co_sk = ui.input(label='Secret Key', password=True, password_toggle_button=True) \
                    .classes('flex-1').props('outlined dense')

            def do_add_company():
                cid = add_co_id.value.strip()
                if not cid or not add_co_name.value.strip() or not add_co_host.value.strip():
                    ui.notify('Company ID, Name, and API Host are required.', type='warning')
                    return
                try:
                    add_company(Company(
                        id=cid,
                        name=add_co_name.value.strip(),
                        api_host=add_co_host.value.strip(),
                        access_key=add_co_ak.value.strip(),
                        secret_key=add_co_sk.value.strip(),
                    ))
                    ui.notify(f'Company {add_co_name.value} added.', type='positive')
                    add_co_id.value = ''
                    add_co_name.value = ''
                    add_co_host.value = ''
                    add_co_ak.value = ''
                    add_co_sk.value = ''
                    _refresh_companies()
                except ValueError as ex:
                    ui.notify(str(ex), type='negative')

            with ui.row().classes('w-full justify-end'):
                ui.button('Add Company', on_click=do_add_company) \
                    .style(f'background-color: {CLR_PRIMARY} !important')


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app():
    """Register all pages. Called from main.py before ui.run()."""
    # Pages are registered by their @ui.page decorators above when this module is imported.
    # This function exists as the explicit entry point for main.py.
    pass
