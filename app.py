import streamlit as st

from data_sources import mlb_client, cache
from core import models, highlight_detector, narrative


st.title("Game Highlights")
