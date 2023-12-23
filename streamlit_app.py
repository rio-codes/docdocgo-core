import os

import streamlit as st

from components.llm import CallbackHandlerDDGStreamlit
from docdocgo import get_bot_response
from utils.helpers import DELIMITER, extract_chat_mode_from_query, parse_query
from utils.prepare import TEMPERATURE
from utils.streamlit.helpers import status_config, write_slowly
from utils.streamlit.prepare import prepare_app
from utils.strings import limit_number_of_characters
from utils.type_utils import ChatState, chat_modes_needing_llm

# Run just once
if "chat_state" not in st.session_state:
    prepare_app()

# For convenience
chat_state: ChatState = st.session_state.chat_state

# Page config
page_icon = "🦉"  # random.choice("🤖🦉🦜🦆🐦")
st.set_page_config(page_title="DocDocGo", page_icon=page_icon)
st.title("DocDocGo")

# Sidebar
with st.sidebar:
    # Set the env variable for the OpenAI API key.
    # Init value of text field is determined by the init value of the env variable.
    with st.expander(
        "OpenAI API Key", expanded=not st.session_state.llm_api_key_ok_status
    ):
        user_openai_api_key = st.text_input(
            "OpenAI API Key",
            label_visibility="collapsed",
            key="openai_api_key",
            type="password",
        )

        # If the user has entered a non-empty OpenAI API key, use it
        if user_openai_api_key:
            # If a non-empty correct unlock pwd was entered, keep using the
            # default key but unlock the full settings
            if user_openai_api_key == os.getenv(
                "BYPASS_SETTINGS_RESTRICTIONS_PASSWORD"
            ):
                user_openai_api_key = ""
                if not st.session_state.allow_all_settings_for_default_key:
                    st.session_state.allow_all_settings_for_default_key = True
                    st.session_state.llm_api_key_ok_status = True # collapse the key field
                    st.rerun() # otherwise won't collapse until next interaction
            else:
                # Otherwise, use the key entered by the user as the OpenAI API key
                os.environ["OPENAI_API_KEY"] = user_openai_api_key

        # If the user has not entered a key (or entered the unlock pwd) use the default
        if not user_openai_api_key:
            os.environ["OPENAI_API_KEY"] = st.session_state.openai_api_key_init_value

        is_community_key = (
            st.session_state.openai_api_key_init_value
            and not user_openai_api_key
            and not st.session_state.allow_all_settings_for_default_key
        )
        if is_community_key:
            "Using the default OpenAI API key (some settings are restricted)"
            "[Get your OpenAI API key](https://platform.openai.com/account/api-keys)"
        elif not os.getenv("OPENAI_API_KEY"):
            "To use this app, you'll need an OpenAI API key."
            "[Get an OpenAI API key](https://platform.openai.com/account/api-keys)"

    # Settings
    with st.expander("Settings", expanded=False):
        model_options = ["gpt-3.5-turbo-1106", "gpt-4-1106-preview"]
        if is_community_key:
            model_options = model_options[:1]
        # TODO: adjust context length (for now assume 16k)
        chat_state.bot_settings.model_name = st.selectbox(
            "Language model", model_options, disabled=is_community_key
        )

        # Temperature
        chat_state.bot_settings.temperature = st.slider(
            "Temperature",
            min_value=0.0,
            max_value=2.0,
            value=TEMPERATURE,
            step=0.1,
            format="%f",
        )
        if chat_state.bot_settings.temperature >= 1.5:
            st.caption(":red[Very high temperatures can lead to **jibberish**]")
        elif chat_state.bot_settings.temperature >= 1.0:
            st.caption(":orange[Consider a lower temperature if precision is needed]")

    # Resources
    with st.expander("Resources", expanded=True):
        "[Command Cheatsheet](https://github.com/reasonmethis/docdocgo-core/blob/main/docs/command-cheatsheet.md)"
        "[Full Docs](https://github.com/reasonmethis/docdocgo-core/blob/main/README.md)"

# If no message has been entered yet, show the intro
if not chat_state.chat_and_command_history:
    welcome_placeholder = st.empty()
    with welcome_placeholder.container():
        for i in range(3):
            st.write("")
        st.write("Welcome! To see what I can do, type")
        st.subheader("/help")
        st.write("")
        st.caption(
            ":red[⮟]:grey[ Tip: See your current **doc collection** in the chat box]"
        )

# Show previous exchanges
for full_query, answer in chat_state.chat_and_command_history:
    with st.chat_message("user"):
        st.markdown(full_query)
    with st.chat_message("assistant"):
        st.markdown(answer)

# Check if the user has entered a query
collection_name = chat_state.vectorstore.name
full_query = st.chat_input(f"{limit_number_of_characters(collection_name, 35)}/")
if not (full_query):
    # If no message from the user, check if we should run an initial test query
    if not chat_state.chat_and_command_history and os.getenv(
        "INITIAL_TEST_QUERY_STREAMLIT"
    ):
        full_query = os.getenv("INITIAL_TEST_QUERY_STREAMLIT")
    else:
        st.stop()

#### The rest will only run once the user has entered a query ####

# Display the user's query
with st.chat_message("user"):
    st.markdown(full_query)
    # Remove the intro message if this is the first query
    if not chat_state.chat_and_command_history:
        welcome_placeholder.empty()

# Parse the query to extract command id & search params, if any
adjusted_full_query = "/help" if full_query.strip().lower() == "help" else full_query
query, chat_mode = extract_chat_mode_from_query(adjusted_full_query)
query, search_params = parse_query(query)
chat_state.update(chat_mode=chat_mode, message=query, search_params=search_params)

# Get and display response from the bot
with st.chat_message("assistant"):
    # Prepare status container and display initial status
    try:
        status = st.status(status_config[chat_mode]["thinking.header"])
        status.write(status_config[chat_mode]["thinking.body"])
    except KeyError:
        status = None

    # Prepare container and callback handler for showing streaming response
    message_placeholder = st.empty()
    callback_handler = CallbackHandlerDDGStreamlit(message_placeholder)
    chat_state.callbacks[1] = callback_handler
    try:
        response = get_bot_response(chat_state)
        answer = response["answer"]

        # Check if this is the first time we got a response from the LLM
        if (
            not st.session_state.llm_api_key_ok_status
            and chat_mode in chat_modes_needing_llm
        ):
            # Set a temp value to trigger a rerun to collapse the API key field
            st.session_state.llm_api_key_ok_status = "RERUN_PLEASE"

        # Display non-streaming responses slowly (in particular avoids chat prompt flicker)
        if chat_mode not in chat_modes_needing_llm:
            write_slowly(message_placeholder, answer)

        # Display the "complete" status
        if status:
            status.update(
                label=status_config[chat_mode]["complete.header"], state="complete"
            )
            status.write(status_config[chat_mode]["complete.body"])

        # Add the response to the chat history if needed
        if not response.get("skip_chat_history", False):
            chat_state.chat_history.append((query, answer))
    except Exception as e:
        # Display the "error" status
        if status:
            status.update(label=status_config[chat_mode]["error.header"], state="error")
            status.write(status_config[chat_mode]["error.body"])

        # Add the error message to the likely incomplete response
        answer = f"Apologies, an error has occurred:\n```\n{e}\n```"
        print(f"{answer}\n{DELIMITER}")

        if callback_handler.buffer:
            answer = f"{callback_handler.buffer}\n\n{answer}"

        # Display the response with the error message
        message_placeholder.markdown(answer)

        # Stop this run
        if os.getenv("RERAISE_EXCEPTIONS"):
            raise e
        st.stop()
    finally:
        # Update the full chat history
        chat_state.chat_and_command_history.append((full_query, answer))

# Update iterative research data
if "ws_data" in response:
    chat_state.ws_data = response["ws_data"]
# TODO: update in flask app as well

# Update vectorstore if needed
if "vectorstore" in response:
    chat_state.vectorstore = response["vectorstore"]

# If this was the first exchange, rerun to remove the intro messages
if len(chat_state.chat_and_command_history) == 1:
    st.rerun()

# If this was the first LLM response, rerun to collapse the OpenAI API key field
if st.session_state.llm_api_key_ok_status == "RERUN_PLEASE":
    st.session_state.llm_api_key_ok_status = True
    st.rerun()

# If the user has switched to a different db, rerun to display the new db name
if collection_name != chat_state.vectorstore.name:
    st.rerun()
