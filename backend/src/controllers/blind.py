from ..services import blind
class BlindController:
    list_batches=staticmethod(blind.list_batches)
    batch=staticmethod(blind.batch_detail)
    create_batch=staticmethod(blind.create_batch)
    replace_inputs=staticmethod(blind.replace_inputs)
    list_applications=staticmethod(blind.list_applications)
    application=staticmethod(blind.application_detail)
    create_application=staticmethod(blind.create_application)
    retry=staticmethod(blind.retry_application)
    unblind=staticmethod(blind.request_unblind)
    export=staticmethod(blind.blind_csv)
