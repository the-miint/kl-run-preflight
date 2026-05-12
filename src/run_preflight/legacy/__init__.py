"""Legacy omnibus-CSV bridging code (parser, reconstructor, format registry)."""


class LegacyExtraColumnWarning(UserWarning):
    """Warning raised when unrecognized columns are carried as extras.

    Emitted in two places:

      - When loading a legacy CSV: any [Data] column not defined by the
        format's view is stored verbatim in legacy_extra_column.
      - When writing a legacy CSV: any extras stored in legacy_extra_column
        for the run are appended to the [Data] section on output.

    Consumers can filter this class specifically (without silencing all
    UserWarnings) via warnings.filterwarnings.
    """
