import tomlkit
import logging
from tomlkit import document, table, comment, dumps

from assignment_backup.configuration.model import Config

_config: Config = None
logger = logging.getLogger(__name__)

CONFIG_FILENAME = "assignment_backup.toml"


def prepare_toml_doc():
    """
    Creates a default TOML document with predefined sections, keys, and comments.
    """
    doc = document()

    # [general] section
    general = table()
    general.add(comment(" General configuration settings "))
    general.add(comment(" the class code you'll be backing up from."))
    general.add(comment(" valid options: cs1050, cs2050"))
    general.add("class_code", "")
    general.add(comment(" how long to attempt running a program before moving on"))
    general.add(comment(" express in seconds (e.g 5 second timeout = 5)"))
    general.add("execution_timeout", 5)
    general.add(comment(" when to invalidate the roster cache. e.g if roster data is X days old, then regen it"))
    general.add(comment(" You can change this value to anything below 1 to always invalidate the roster cache. "))
    general.add("roster_invalidation_days", 14)
    general.add(
        comment(" if your class (or particular use case) uses header files, toggle this to cache necessary files"))
    general.add(comment(" cs2050 as an example needs header files"))
    general.add("use_header_files", True)
    general.add(comment(
        "if your class (or particular use case) is expecting Makefiles, toggle this to cache files and compile "
        "correctly"))
    general.add("use_makefile", True)
    general.add(comment(" Whether or not the script should attempt to compile submissions."))
    general.add("compile_submissions", True)
    general.add(comment(" Whether or not the script should attempt to execute submissions. "))
    general.add("execute_submissions", True)
    general.add(comment(" Whether or not the script should also generate a valgrind output of the submission."))
    general.add(comment(" You need to have previously enabled submission execution for this to work."))
    general.add(comment(" Whether or not the script should clear existing lab backups."))
    general.add("generate_valgrind_output", True)
    general.add("clear_existing_backups", True)
    general.add(comment(
        "If you're executing submissions, this string will be inserted into stdio during execution. Leave blank to "
        "not insert anything."))
    general.add("input_string", "")
    general.add(comment(" Whether or not to check Canvas for attendance points."))
    general.add("check_attendance", False)
    doc["general"] = general

    # [paths] section
    paths = table()
    paths.add(comment(" - Path to the SQLite3 DB instance of the MUCSv2 course instance"))
    paths.add("sqlite_db_path", "data/{class_code}.db")
    paths.add(comment(" the local storage dir will have the class code preappended to it"))
    paths.add("local_storage_dir", "_local_labs")
    # paths.add(comment(" the hellbender lab dir will have the class code appended to it"))
    # paths.add(
        # comment(" e.g if your lab dir is \"/cluster/pixstor/class/\" and your class code is \"cs1050\", it'll be"))
    # paths.add(comment(" \"/cluster/pixstor/class/cs1050\""))
    # paths.add("hellbender_lab_dir", "/cluster/pixstor/class/")
    paths.add(comment(" created in the local storage dir"))
    paths.add("cache_dir", "cache")
    doc["paths"] = paths

    # [canvas] section
    canvas = table()
    canvas.add(comment(" API Prefix for connecting to Canvas"))
    canvas.add(comment(" This shouldn't need to change for MUCS purposes"))
    canvas.add("api_prefix", "https://umsystem.instructure.com/api/v1/")
    canvas.add(comment(" API Token associated with your Canvas user"))
    canvas.add(comment(
        "https://community.canvaslms.com/t5/Canvas-Basics-Guide/How-do-I-manage-API-access-tokens-in-my-user-account"
        "/ta-p/615312"))
    canvas.add(comment(" You should keep this secret."))
    canvas.add("api_token", "")
    canvas.add(comment(" The Canvas course ID associated with the course you're grading for"))
    canvas.add(comment(
        " This can be retrieved by getting it from the course URL: e.g https://umsystem.instructure.com/courses/306521"))
    canvas.add("course_id", -1)
    canvas.add(comment(" The naming scheme for the assignment associated with attendance in labs/assignments."))
    canvas.add("attendance_assignment_name_scheme", "")
    canvas.add(
        comment(" How many points a student should have in the attendance assignment to qualify for compilation. "))
    canvas.add("attendance_assignment_point_criterion", 1.0)
    doc["canvas"] = canvas

    with open(CONFIG_FILENAME, 'w') as f:
        f.write(dumps(doc))
    logger.debug(f"Created default {CONFIG_FILENAME}")


def get_config() -> Config:
    """
    Retrieves the instance of the initialized Config.
    :returns: The Config object
    :raises RuntimeError: If load_config was not called.
    """
    if _config is None:
        logger.critical("This function was called before initialization was completed!")
        raise RuntimeError("The configuration has not been loaded into memory!")
    return _config


def load_config():
    with open(CONFIG_FILENAME, 'r') as f:
        logger.debug(f"Reading contents of {CONFIG_FILENAME}")
        content = f.read()
    doc = tomlkit.parse(content)
    logger.debug("Loading configuration into memory")

    global _config

    # Extract values from the TOML document
    general = doc.get('general', {})
    paths = doc.get('paths', {})
    canvas = doc.get('canvas', {})

    config_obj = Config(class_code=general.get('class_code', ""),
                        execution_timeout=general.get('execution_timeout', -1),
                        roster_invalidation_days=general.get('roster_invalidation_days', -1),
                        use_header_files=general.get('use_header_files', True),
                        use_makefile=general.get('use_makefile', True),
                        compile_submissions=general.get('compile_submissions', True),
                        execute_submissions=general.get('execute_submissions', True),
                        generate_valgrind_output=general.get('generate_valgrind_output', True),
                        clear_existing_backups=general.get('clear_existing_backups', True),
                        input_string=general.get("input_string", ""),
                        check_attendance=general.get("check_attendance", False),
                        local_storage_dir=paths.get('local_storage_dir', ""),
                        sqlite_db=paths.get('sqlite_db_path', ""),
                        cache_dir=paths.get('cache_dir', "cache"), api_prefix=canvas.get('api_prefix', ""),
                        api_token=canvas.get('api_token', ""), course_id=canvas.get('course_id', -1),
                        attendance_assignment_name_scheme=canvas.get('attendance_assignment_name_scheme', ""),
                        attendance_assignment_point_criterion=canvas.get("attendance_assignment_point_criterion", 1))
    _config = config_obj
