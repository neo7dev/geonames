# ! Add additional imports here for all the tables in the database. This allows SQLAlchemy to find all the models when running migrations, and also provides a convenient single import location for all models in the codebase.
from .allcountries import AllCountries  # noqa: F401
from .cities500 import Cities500  # noqa: F401
from .cities1000 import Cities1000  # noqa: F401
from .cities5000 import Cities5000  # noqa: F401
from .cities15000 import Cities15000  # noqa: F401
from .dataset_metadata import DatasetMetaData  # noqa: F401
from .deletions import Deletions  # noqa: F401
from .geobase import GeoBase  # noqa: F401
from .modifications import Modifications  # noqa: F401
