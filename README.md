# Android TV Background

This is a simple script to retrieve plex or TMDB media background, i developed this to use it with alternative android tv launcher

To use the script, you have to specify : 
- your plex token and plex server url
- Your TMDB API Read Access Token

The scripts retrieves the background of the latests shows (movies or tv shows)
it resizes the image, add an overlay and add text on top

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/434e7077-daaf-41b6-8e43-08bf380fb2d3)

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/da313f5f-287f-430f-b3fd-f56e5f139e40)

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/25565525-1958-4944-b47f-b06344d22914)

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/b96f3e83-29a6-4e3f-a202-2e33bc80aa8f)

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/b28900a4-4776-4aae-b631-e30334d932dd)

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/e0410589-81a4-40ac-a55d-8fd6eb061721)



How to :
- install python and dependencies
- Download the content of this repository and put the script and images in a specific folder
- Edit the python script to specify you credentials,
- For plex media you can specify the number of poster to generate, specify if you want to include movies and tv, specify if you want latest added or latest aired items. You can also edit the code to change the text position or content
- There is two versions of the TMDB script, one without show logo and one without. Shows that do not have the logo on TMDB will just have the title displayed
- As you run one of the script  it will create a new folder called backgrounds and it will create the images automatically. Each time the script runs it will delete the content of the folder and create new images


