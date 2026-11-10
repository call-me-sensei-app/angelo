# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private closed account-producer diagnostics; no exception text, locals or observed values."""
PHASES = frozenset({'transport_observe', 'initial_binding', 'create_target', 'attach_target',
                   'navigate_fab', 'fab_account_menu', 'navigate_account', 'decode_account',
                   'binding_before_close', 'close_target', 'binding_after_close', 'transport_listing',
                   'listing_preflight', 'listing_account_before', 'listing_navigate',
                   'listing_open_download', 'listing_readiness', 'listing_activate', 'listing_decode', 'listing_account_after', 'listing_convert',
                   'transport_search'})
REASONS = frozenset({'AUTH_REQUIRED', 'CLOSED', 'USER_SIGN_IN_PENDING', 'ACCOUNT_PROOF_UNAVAILABLE',
                    'ACCOUNT_TARGET_CLEANUP_UNPROVED', 'PROFILE_OWNER_REQUIRED', 'PROFILE_CHANGED',
                    'PHYSICAL_BINDING_UNPROVED', 'CANCELLED', 'TIMEOUT', 'PROTOCOL',
                    'PAGE_CHANGED', 'OUTSIDE_ACCOUNT_PAGE', 'ACCOUNT_PAGE_NOT_READY',
                    'ACCOUNT_ID_AMBIGUOUS', 'ACCOUNT_ID_INVALID', 'SCAN_LIMIT',
                    'LISTING_PROOF_UNAVAILABLE', 'LISTING_ID_INVALID', 'NAVIGATION_FAILED', 'BUSY',
                    'OBSERVATION_SHAPE', 'OBSERVATION_LIMIT', 'SITE_CHANGED', 'LISTING_CHANGED',
                    'CHOICE_AMBIGUOUS', 'LISTING_OUTSIDE', 'LISTING_NOT_READY', 'LISTING_DOWNLOAD_CONTROL',
                    'LISTING_SCAN_LIMIT', 'LISTING_LABEL_RELATION', 'LISTING_MODAL_HEADING',
                    'LISTING_TITLE_COUNT', 'LISTING_TITLE_SCOPE', 'LISTING_SELLER_COUNT',
                    'LISTING_MODAL_TITLE', 'LISTING_NO_GLB', 'LISTING_CONVERTED_GROUP', 'LISTING_CONVERTED_REGION',
                    'LISTING_ROW_BADGE', 'LISTING_ROW_FILE_SIZE', 'LISTING_ROW_QUALITY',
                    'LISTING_ROW_DOWNLOAD_CONTROL', 'LISTING_LICENSE_AMBIGUOUS', 'LISTING_LICENSE_INVALID',
                    'LISTING_LICENSE_MISSING', 'LISTING_LIBRARY_AMBIGUOUS',
                    'LISTING_BODY_MISSING', 'LISTING_DIALOG_MISSING', 'LISTING_ROWS_MISSING', 'LISTING_GLB_MISSING',
                    'LISTING_DOCUMENT_LOADING', 'LISTING_DOCUMENT_UNVERIFIED'})
TYPES = frozenset({'BrowserUnavailable', 'ListingObservationUnavailable', 'AccountPageUnavailable', 'ProfileUnavailable', 'FabError',
                  'AttributeError', 'TypeError', 'ValueError', 'KeyError', 'RuntimeError',
                  'OSError', 'TimeoutError', 'ImportError', 'NameError'})
MODULES = frozenset({'fab_browser_identity', 'fab_browser_account_page', 'fab_browser_transport',
                     'fab_browser_platform', 'fab_browser_profile', 'fab_browser_process',
                     'fab_browser_account', 'fab_browser_listing_owner', 'fab_browser_listing_page'})
FUNCTIONS = frozenset({'read_current_account', '_read_current_account', '_value', 'decode_account_page',
                       'account_binding', 'profile_generation', 'owned_lease', 'validate_lease',
                       'validate', '_validate', 'observe', '__init__', '_check', 'call', '_call',
                       'listing', '_listing', 'observe_listing', '_observe_listing',
                       'parse_listing_observation', '_mapping', '_text'})


def _listing_details(value):
    """Bounded fixed enums/counts only; frame/loader IDs and observed content have no output field."""
    states={'not_observed','outside_listing','not_ready','dialog_present','download_dialog_opening',
            'download_control_unavailable','scan_limit','document_loading','document_ready'}
    branches=states|{'body_missing','dialog_missing','rows_missing','glb_missing','license_missing'}
    if (type(value)is not dict or set(value)not in ({'open_result','navigation_probes','trace'},
                                            {'open_result','navigation_probes','target_activated','trace'})
        or type(value['open_result'])is not str or value['open_result']not in states
        or ('target_activated'in value and type(value['target_activated'])is not bool)
        or type(value['navigation_probes'])is not int or not 0<=value['navigation_probes']<=4
        or type(value['trace'])is not list or len(value['trace'])>8):return None
    for row in value['trace']:
        if (type(row)is not dict or set(row)not in ({'point','branch','ready','document','dialogs','rows'},
                                              {'point','branch','ready','document','dialogs','rows','render'})
            or type(row['point'])is not str or row['point']not in ('after_navigation','readiness','open','decode')
            or type(row['branch'])is not str or row['branch']not in branches
            or type(row['ready'])is not str or row['ready']not in ('loading','interactive','complete','other','UNOBSERVED')
            or type(row['document'])is not str or row['document']not in ('MATCH','OLD','FRAME_CHANGED','UNOBSERVED')
            or type(row['dialogs'])is not int or not 0<=row['dialogs']<=2
            or type(row['rows'])is not int or not 0<=row['rows']<=24):return None
        if 'render'in row:
            facts=row['render']
            if (type(facts)is not dict or set(facts)!={'visibility','raw_dialogs','hidden_dialogs','opacity_zero_dialogs'}
                or type(facts['visibility'])is not str or facts['visibility']not in ('visible','hidden','prerender','other','UNOBSERVED')
                or any(type(facts[key])is not int or not 0<=facts[key]<=2
                       for key in ('raw_dialogs','hidden_dialogs','opacity_zero_dialogs'))):return None
    return dict(value,trace=[dict(row,render=dict(row['render']))if 'render'in row else dict(row)for row in value['trace']])


def relation_details(value):
    """Facts from one failed bounded IDREF audit, never a label or authorization substitute."""
    keys={'kind','attribute_present','id_count','resolved_count','unique_count','unambiguous_id_count',
          'inside_owner_count','visible_count','tag','expected_label_match','audit_complete'}
    if (type(value)is not dict or set(value)!=keys
        or type(value['kind'])is not str or value['kind']not in ('modal','region')
        or type(value['tag'])is not str or value['tag']not in ('NONE','MULTIPLE','H2','H5','BUTTON','SPAN','DIV','OTHER')
        or any(type(value[key])is not bool for key in ('attribute_present','expected_label_match','audit_complete'))
        or any(type(value[key])is not int or not 0<=value[key]<=2 for key in
               ('id_count','resolved_count','unique_count','unambiguous_id_count','inside_owner_count','visible_count'))):return None
    return dict(value)


def failure_details(error, phase):
    """Actual traceback location only. Unrecognized metadata is a fixed OTHER marker."""
    trace = []
    cursor = error.__traceback__
    while cursor is not None:
        frame = cursor.tb_frame
        module = frame.f_globals.get('__name__', '').rsplit('.', 1)[-1]
        if module in MODULES:
            name = frame.f_code.co_name
            trace.append({'module': module, 'function': name if name in FUNCTIONS else 'OTHER',
                          'line': cursor.tb_lineno})
        cursor = cursor.tb_next
    name = type(error).__name__
    reason = getattr(error, 'listing_reason', None)
    if type(reason) is not str or reason not in REASONS:
        reason = getattr(error, 'account_page_reason', None)
    if type(reason) is not str or reason not in REASONS:
        reason = getattr(error, 'code', None)
    result = {'schema': 1, 'phase': phase if phase in PHASES else 'OTHER',
            'type': name if name in TYPES else 'OTHER',
            'reason': reason if type(reason) is str and reason in REASONS else 'OTHER', 'trace': trace[-12:]}
    listing=_listing_details(getattr(error,'listing_diagnostic',None))
    if listing is not None:result['listing']=listing
    relation=relation_details(getattr(error,'listing_relation',None))
    if relation is not None:result['relation']=relation
    return result


def mapped_failure_details(error, *, phase='transport_observe'):
    """Copy only a closed producer record, otherwise derive the current transport failure."""
    value = getattr(error, 'producer_diagnostic', None)
    if (type(value) is dict and {'schema','phase','type','reason','trace'}<=set(value)<=
            {'schema','phase','type','reason','trace','listing','relation'}
            and type(value['schema']) is int and value['schema'] == 1
            and type(value['phase']) is str and value['phase'] in PHASES
            and type(value['type']) is str and value['type'] in TYPES | {'OTHER'}
            and type(value['reason']) is str and value['reason'] in REASONS | {'OTHER'}
            and type(value['trace']) is list and len(value['trace']) <= 12
            and all(type(row) is dict and set(row) == {'module', 'function', 'line'}
                    and type(row['module']) is str and row['module'] in MODULES
                    and type(row['function']) is str and row['function'] in FUNCTIONS | {'OTHER'}
                    and type(row['line']) is int and 0 < row['line'] <= 1000000
                    for row in value['trace'])):
        result=dict(value, trace=[dict(row) for row in value['trace']])
        result.pop('listing',None);result.pop('relation',None)
        listing=_listing_details(getattr(error,'listing_diagnostic',None))
        if listing is None:listing=_listing_details(value.get('listing'))
        if listing is not None:result['listing']=listing
        relation=relation_details(getattr(error,'listing_relation',None))
        if relation is None:relation=relation_details(value.get('relation'))
        if relation is not None:result['relation']=relation
        return result
    return failure_details(error, phase)
